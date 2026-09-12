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

// Everything a file loads or runs from a string, found by walking the whole
// tree. A separate walk on purpose: `visit` above stops at shapes it cannot
// count (`.each`, a dynamic title), and a load hidden inside one of those must
// still be seen. The syntax tree is what makes this trustworthy where a regex
// is not: `"https://x"; import("fs")` has a `//` inside a string, and a regex
// literal can contain `/*` — both hide real code from a line-based scan.
const RUNTIME_GLOBALS = new Set(["globalThis", "global", "module", "process"]);
const CODE_FROM_STRING = new Set(["eval", "Function", "require", "mainModule", "binding", "dlopen"]);

function loads(file, code) {
  const tree = ts.createSourceFile(file, code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const found = [];
  const add = (node, kind, specifier = null) => {
    const { line, character } = tree.getLineAndCharacterOfPosition(node.getStart(tree));
    found.push({ kind, specifier, line: line + 1, column: character + 1,
      text: node.getText(tree).replace(/\s+/g, " ").slice(0, 120) });
  };
  const literal = (node) => (node && ts.isStringLiteralLike(node) ? node.text : null);
  // An import that only names types is erased before anything runs.
  const typeOnly = (node) => node.importClause?.isTypeOnly ||
    (node.importClause && !node.importClause.name && node.importClause.namedBindings &&
     ts.isNamedImports(node.importClause.namedBindings) &&
     node.importClause.namedBindings.elements.length > 0 &&
     node.importClause.namedBindings.elements.every((item) => item.isTypeOnly));

  function walk(node) {
    if (ts.isImportDeclaration(node)) {
      if (!typeOnly(node)) add(node, "module", literal(node.moduleSpecifier));
    } else if (ts.isExportDeclaration(node) && node.moduleSpecifier) {
      if (!node.isTypeOnly) add(node, "module", literal(node.moduleSpecifier));
    } else if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
      add(node, "module", literal(node.moduleReference.expression));
    } else if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const specifier = literal(node.arguments[0]);
      add(node, specifier === null ? "dynamic" : "module", specifier);
    } else if (ts.isIdentifier(node) && node.text === "require" &&
               !(ts.isPropertyAccessExpression(node.parent) && node.parent.name === node)) {
      const call = ts.isCallExpression(node.parent) && node.parent.expression === node;
      const specifier = call ? literal(node.parent.arguments[0]) : null;
      // `require("x")` is an import by another name; `require(name)` or a bare
      // `require` passed around is a load nobody can read statically.
      add(call ? node.parent : node, specifier === null ? "dynamic" : "module", specifier);
    } else if (ts.isIdentifier(node) && (node.text === "eval" || node.text === "Function") &&
               !(ts.isPropertyAccessExpression(node.parent) && node.parent.name === node) &&
               !(ts.isPropertyAssignment(node.parent) && node.parent.name === node)) {
      add(node, "code", node.text);
    } else if ((ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) &&
               ts.isIdentifier(node.expression) && RUNTIME_GLOBALS.has(node.expression.text)) {
      const name = ts.isPropertyAccessExpression(node) ? node.name.text : literal(node.argumentExpression);
      // `globalThis.eval`, `module.require`, `process.mainModule`, and
      // `globalThis[anything computed]` all reach the same place by a side door.
      if (name === null || CODE_FROM_STRING.has(name)) add(node, "code", name ?? "computed");
    } else if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) &&
               node.expression.name.text === "constructor") {
      // `(async () => {}).constructor("...")` is `new Function` without the name.
      add(node, "code", "constructor");
    }
    ts.forEachChild(node, walk);
  }
  walk(tree);
  return found;
}

// One process inventories both sides; JSON avoids shell quoting and work files.
// An optional third element asks for the load inventory too. Only the graph
// asks, so every other caller gets exactly the output it always did.
const [sourceFiles, convertedFiles, options = {}] = JSON.parse(readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify([sourceFiles, convertedFiles].map((files) => Object.fromEntries(
  Object.entries(files).map(([file, code]) => [file, options.loads
    ? { ...inventory(file, code), loads: loads(file, code) }
    : inventory(file, code)]),
))));
