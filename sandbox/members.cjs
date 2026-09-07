// Static public-API inventory for the suite report (step 9.3).
// Parse text only; never import or execute submitted code — same rule as parity.cjs.
// "Public API" here means what another file could reach: exported top-level
// functions and constants, and the members of a class that are not private or
// protected. That is the surface a conversion is allowed to change but not to
// lose silently.
const { readFileSync } = require("node:fs");
const ts = require("typescript");

const HIDDEN = new Set([ts.SyntaxKind.PrivateKeyword, ts.SyntaxKind.ProtectedKeyword]);

function inventory(file, code) {
  const tree = ts.createSourceFile(file, code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const result = { classes: [], top: [], issues: [] };
  const position = (node) => {
    const { line, character } = tree.getLineAndCharacterOfPosition(node.getStart(tree));
    return { line: line + 1, column: character + 1 };
  };
  const issue = (node, message) => result.issues.push({ ...position(node), message });
  for (const diagnostic of tree.parseDiagnostics) {
    const { line, character } = tree.getLineAndCharacterOfPosition(diagnostic.start);
    result.issues.push({ line: line + 1, column: character + 1,
      message: ts.flattenDiagnosticMessageText(diagnostic.messageText, " ") });
  }

  const has = (node, kind) => (node.modifiers ?? []).some((m) => m.kind === kind);
  const hidden = (node) => (node.modifiers ?? []).some((m) => HIDDEN.has(m.kind));
  const exported = (node) => has(node, ts.SyntaxKind.ExportKeyword);
  // A member whose name is computed (`[key]()`) or a #private field is not a
  // name we can compare across two files, so it is reported as an issue rather
  // than counted as present or missing.
  const named = (node) => {
    const name = node.name;
    if (!name || ts.isPrivateIdentifier(name)) return null;
    if (ts.isIdentifier(name) || ts.isStringLiteralLike(name)) return name.text;
    return null;
  };
  const kindOf = (member) => {
    if (ts.isMethodDeclaration(member) || ts.isMethodSignature(member)) return "method";
    if (ts.isGetAccessor(member)) return "getter";
    if (ts.isSetAccessor(member)) return "setter";
    return "property";
  };

  for (const statement of tree.statements) {
    if (ts.isClassDeclaration(statement)) {
      const entry = { name: statement.name?.text ?? "(default export)",
                      exported: exported(statement), members: [] };
      for (const member of statement.members) {
        if (ts.isConstructorDeclaration(member)) {
          // `constructor(public readonly page: Page)` declares a public field.
          for (const parameter of member.parameters) {
            if (!parameter.modifiers?.length || hidden(parameter)) continue;
            const name = named(parameter);
            if (name) entry.members.push({ name, kind: "property", static: false, ...position(parameter) });
          }
          continue;
        }
        if (hidden(member) || ts.isSemicolonClassElement(member)) continue;
        const name = named(member);
        if (name === null) {
          issue(member, "a computed or #private member name cannot be compared across files");
          continue;
        }
        entry.members.push({ name, kind: kindOf(member),
                             static: has(member, ts.SyntaxKind.StaticKeyword), ...position(member) });
      }
      result.classes.push(entry);
    } else if (ts.isFunctionDeclaration(statement) && exported(statement) && statement.name) {
      result.top.push({ name: statement.name.text, kind: "function", ...position(statement) });
    } else if (ts.isVariableStatement(statement) && exported(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        if (ts.isIdentifier(declaration.name)) {
          result.top.push({ name: declaration.name.text, kind: "constant", ...position(declaration) });
        }
      }
    }
  }
  return result;
}

// Same protocol as parity.cjs: a list of {path: source} maps in, the same list
// of {path: inventory} maps out, so one process inventories both sides at once.
const groups = JSON.parse(readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(groups.map((files) => Object.fromEntries(
  Object.entries(files).map(([file, code]) => [file, inventory(file, code)]),
))));
