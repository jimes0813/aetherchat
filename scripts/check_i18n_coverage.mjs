#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from '../frontend/node_modules/typescript/lib/typescript.js';

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sourceRoot = path.join(repositoryRoot, 'frontend', 'src');
const i18nFile = path.join(sourceRoot, 'i18n.ts');
const visibleLiteralAttributes = new Set(['alt', 'aria-label', 'placeholder', 'title']);
const cjkPattern = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/u;

function englishUiKeys() {
  const sourceText = fs.readFileSync(i18nFile, 'utf8');
  const sourceFile = ts.createSourceFile(i18nFile, sourceText, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const keys = new Set();
  sourceFile.forEachChild((node) => {
    if (!ts.isVariableStatement(node)) return;
    for (const declaration of node.declarationList.declarations) {
      if (declaration.name.getText(sourceFile) !== 'englishUi' || !declaration.initializer || !ts.isObjectLiteralExpression(declaration.initializer)) continue;
      for (const property of declaration.initializer.properties) {
        if (!ts.isPropertyAssignment(property)) continue;
        if (ts.isStringLiteral(property.name) || ts.isNoSubstitutionTemplateLiteral(property.name)) {
          keys.add(property.name.text);
        }
      }
    }
  });
  return keys;
}

function collectSourceFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return collectSourceFiles(entryPath);
    if (entry.name.includes('.test.') || entry.name.includes('.spec.')) {
      return [];
    }
    if (entry.name.endsWith('.tsx') || entry.name.endsWith('.ts')) {
      return [entryPath];
    }
    return [];
  });
}

function isUiOrTranslateCall(expression) {
  return ts.isCallExpression(expression)
    && ts.isIdentifier(expression.expression)
    && (expression.expression.text === 'ui' || expression.expression.text === 't' || expression.expression.text === 'localize');
}

function location(sourceFile, node) {
  const start = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
  return `${path.relative(repositoryRoot, sourceFile.fileName)}:${start.line + 1}:${start.character + 1}`;
}

function renderedChineseLiterals(expression) {
  if (!expression) return [];
  if (ts.isStringLiteral(expression) || ts.isNoSubstitutionTemplateLiteral(expression)) {
    return cjkPattern.test(expression.text) ? [expression] : [];
  }
  if (ts.isTemplateExpression(expression)) {
    return cjkPattern.test(expression.getText()) ? [expression] : [];
  }
  if (ts.isCallExpression(expression)) {
    if (ts.isIdentifier(expression.expression)
      && (expression.expression.text === 'ui' || expression.expression.text === 't')) {
      return [];
    }
    return [];
  }
  if (ts.isConditionalExpression(expression)) {
    return [
      ...renderedChineseLiterals(expression.whenTrue),
      ...renderedChineseLiterals(expression.whenFalse),
    ];
  }
  if (ts.isBinaryExpression(expression)) {
    if ([
      ts.SyntaxKind.AmpersandAmpersandToken,
      ts.SyntaxKind.BarBarToken,
      ts.SyntaxKind.QuestionQuestionToken,
      ts.SyntaxKind.PlusToken,
    ].includes(expression.operatorToken.kind)) {
      return [
        ...renderedChineseLiterals(expression.left),
        ...renderedChineseLiterals(expression.right),
      ];
    }
    return [];
  }
  if (ts.isParenthesizedExpression(expression)
    || ts.isAsExpression(expression)
    || ts.isNonNullExpression(expression)) {
    return renderedChineseLiterals(expression.expression);
  }
  return [];
}

const problems = [];
const translatedUi = englishUiKeys();
for (const fileName of collectSourceFiles(sourceRoot)) {
  const sourceText = fs.readFileSync(fileName, 'utf8');
  const kind = fileName.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sourceFile = ts.createSourceFile(fileName, sourceText, ts.ScriptTarget.Latest, true, kind);

  function visit(node) {
    if (ts.isJsxText(node)) {
      const text = node.getText(sourceFile).replace(/\s+/gu, ' ').trim();
      if (text && cjkPattern.test(text)) {
        problems.push(`${location(sourceFile, node)} untranslated JSX text: ${JSON.stringify(text)}`);
      }
    }

    if (ts.isJsxAttribute(node)) {
      const attributeName = node.name.getText(sourceFile);
      const value = node.initializer && ts.isStringLiteral(node.initializer)
        ? node.initializer.text.trim()
        : '';
      if (visibleLiteralAttributes.has(attributeName) && value && cjkPattern.test(value)) {
        problems.push(`${location(sourceFile, node)} untranslated ${attributeName}: ${JSON.stringify(value)}`);
      }
      if (visibleLiteralAttributes.has(attributeName)
        && node.initializer
        && ts.isJsxExpression(node.initializer)) {
        for (const literal of renderedChineseLiterals(node.initializer.expression)) {
          problems.push(`${location(sourceFile, literal)} untranslated ${attributeName} expression: ${JSON.stringify(literal.getText(sourceFile))}`);
        }
      }
    }

    if (ts.isJsxExpression(node)
      && (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))) {
      for (const literal of renderedChineseLiterals(node.expression)) {
        problems.push(`${location(sourceFile, literal)} untranslated JSX expression: ${JSON.stringify(literal.getText(sourceFile))}`);
      }
    }

    if (ts.isCallExpression(node)
      && ts.isIdentifier(node.expression)
      && node.expression.text === 'ui'
      && node.arguments.length > 0) {
      const textArgument = node.arguments[0];
      if ((ts.isStringLiteral(textArgument) || ts.isNoSubstitutionTemplateLiteral(textArgument))
        && cjkPattern.test(textArgument.text)
        && !translatedUi.has(textArgument.text)) {
        problems.push(`${location(sourceFile, textArgument)} ui(...) text has no en-US mapping: ${JSON.stringify(textArgument.text)}`);
      }
    }

    if (ts.isCallExpression(node)
      && ts.isIdentifier(node.expression)
      && node.expression.text === 'confirmAction'
      && node.arguments.length > 0) {
      const message = node.arguments[0];
      if (ts.isStringLiteral(message) || ts.isNoSubstitutionTemplateLiteral(message) || ts.isTemplateExpression(message)) {
        if (cjkPattern.test(message.getText(sourceFile))) {
          problems.push(`${location(sourceFile, message)} confirmAction(...) must use ui(...) / t(...): ${JSON.stringify(message.getText(sourceFile))}`);
        }
      } else if (isUiOrTranslateCall(message)) {
        const textArgument = message.arguments[0];
        if ((ts.isStringLiteral(textArgument) || ts.isNoSubstitutionTemplateLiteral(textArgument))
          && cjkPattern.test(textArgument.text)
          && !translatedUi.has(textArgument.text)) {
          problems.push(`${location(sourceFile, textArgument)} confirmAction ui(...) has no en-US mapping: ${JSON.stringify(textArgument.text)}`);
        }
      }
    }

    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
}

if (problems.length > 0) {
  console.error('User-visible Chinese JSX literals must use t(...) or ui(...):');
  for (const problem of problems) console.error(`- ${problem}`);
  process.exit(1);
}

console.log('i18n coverage gate passed: JSX, confirmAction, and command-palette ui(...) strings have en-US mappings.');
