// Static inventory for gate 4. Parse text only; never import or execute submitted code.
// TypeScript is already pinned in this sandbox for the compile gate.
const { readFileSync } = require("node:fs");
const ts = require("typescript");

function inventory(file, code) {
  const tree = ts.createSourceFile(file, code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const result = { tests: [], outside: [], issues: [] };
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

  // Recognize the usual globals plus named imports such as `expect as check`.
  // This is syntax analysis, not symbol resolution or runtime test discovery.
  const aliases = new Map([["xit", "it.skip"], ["xtest", "test.skip"],
    ["xdescribe", "describe.skip"], ["fit", "it.only"], ["fdescribe", "describe.only"]]);
  for (const statement of tree.statements) {
    if (!ts.isImportDeclaration(statement) ||
        !["chai", "mocha", "@playwright/test", "@jest/globals"].includes(statement.moduleSpecifier.text)) continue;
    const bindings = statement.importClause?.namedBindings;
    if (statement.importClause?.name) aliases.set(statement.importClause.name.text, "");
    if (bindings && ts.isNamespaceImport(bindings)) aliases.set(bindings.name.text, "");
    if (bindings && ts.isNamedImports(bindings)) {
      for (const item of bindings.elements) aliases.set(item.name.text, (item.propertyName ?? item.name).text);
    }
  }
  function callee(node) {
    if (ts.isIdentifier(node)) return aliases.get(node.text) ?? node.text;
    if (ts.isPropertyAccessExpression(node)) return [callee(node.expression), node.name.text].filter(Boolean).join(".");
    if (ts.isCallExpression(node)) return `${callee(node.expression)}()`;
    return "";
  }

  function visit(node, suites = [], owner = null, disabled = false) {
    if (ts.isCallExpression(node)) {
      const name = callee(node.expression);
      const suite = /^(describe|test\.describe)(\.(only|skip|fixme|serial|parallel))*$/.test(name);
      const test = /^(it|test)(\.(only|skip|fixme|todo|concurrent))*$/.test(name);
      if (/^(it|test|describe)(\.|$)/.test(name) && name.includes(".each")) {
        issue(node, "Parameterized .each declarations need review; runtime case counts are unknown");
        return;
      }
      if (owner && /^(test\.(skip|fixme)|test\.info\(\)\.(skip|fixme))$/.test(name)) {
        issue(node, `Conditional disabling inside test ${JSON.stringify(owner.name)} needs review`);
        return;
      }
      if (suite || test) {
        const title = node.arguments[0];
        const callback = node.arguments.at(-1);
        if (!title || !ts.isStringLiteralLike(title)) {
          issue(node, "A dynamic test/suite title cannot be matched statically; review parity");
          return;
        }
        const path = [...suites, title.text];
        const skipped = disabled || /\.(skip|fixme|todo)(\.|$)/.test(name);
        const inline = callback && (ts.isArrowFunction(callback) || ts.isFunctionExpression(callback));
        if (!inline && (suite || node.arguments.length > 1)) {
          issue(node, `An inline callback is required to count assertions in ${JSON.stringify(path)}`);
        }
        if (suite) {
          if (inline) visit(callback, path, owner, skipped);
        } else {
          const entry = { name: path, ...position(node), disabled: skipped || !inline, assertions: [] };
          result.tests.push(entry);
          if (inline) visit(callback, suites, entry, skipped);
        }
        return;
      }
      const expectCall = /^(expect|expect\.(soft|poll))$/.test(name);
      if (expectCall || /^assert(\.[\w$]+)?$/.test(name)) {
        // Walk to the end of the assertion chain for a useful source excerpt.
        // Count expect(x).to.equal(y) once, not once per chained method call.
        let expression = node;
        while ((ts.isPropertyAccessExpression(expression.parent) || ts.isCallExpression(expression.parent)) &&
               expression.parent.expression === expression) expression = expression.parent;
        // A bare expect(x) has no matcher and does not preserve an assertion.
        if (!expectCall || expression !== node) {
          (owner ? owner.assertions : result.outside).push({ ...position(node),
            text: expression.getText(tree).replace(/\s+/g, " ") });
        }
      }
    }
    ts.forEachChild(node, (child) => { visit(child, suites, owner, disabled); });
  }
  visit(tree);
  return result;
}

// One process inventories both sides; JSON avoids shell quoting and work files.
const groups = JSON.parse(readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(groups.map((files) => Object.fromEntries(
  Object.entries(files).map(([file, code]) => [file, inventory(file, code)]),
))));
