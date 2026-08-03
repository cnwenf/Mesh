import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import ts from 'typescript';
import { matchPath } from 'react-router';
import { describe, expect, it } from 'vitest';
import { APP_ROUTE_MANIFEST } from '../appRouteManifest';
import type { AppRouteAccess, AppRouteKind } from '../appRouteManifest';

interface ExtractedRoute {
  readonly pattern: string;
  readonly element: string;
  readonly access: AppRouteAccess;
  readonly kind: AppRouteKind;
  readonly redirectTo?: string;
}

const APP_SOURCE = readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf8');

function tagName(node: ts.JsxOpeningLikeElement): string {
  return node.tagName.getText();
}

function stringAttribute(node: ts.JsxOpeningLikeElement, name: string): string | undefined {
  const attribute = node.attributes.properties.find(
    (candidate): candidate is ts.JsxAttribute =>
      ts.isJsxAttribute(candidate) && candidate.name.getText() === name,
  );
  if (attribute?.initializer === undefined) return undefined;
  if (ts.isStringLiteral(attribute.initializer)) return attribute.initializer.text;
  if (
    ts.isJsxExpression(attribute.initializer) &&
    attribute.initializer.expression !== undefined &&
    ts.isStringLiteral(attribute.initializer.expression)
  ) {
    return attribute.initializer.expression.text;
  }
  return undefined;
}

function hasAttribute(node: ts.JsxOpeningLikeElement, name: string): boolean {
  return node.attributes.properties.some(
    (candidate) => ts.isJsxAttribute(candidate) && candidate.name.getText() === name,
  );
}

function elementAttribute(node: ts.JsxOpeningLikeElement): string {
  const attribute = node.attributes.properties.find(
    (candidate): candidate is ts.JsxAttribute =>
      ts.isJsxAttribute(candidate) && candidate.name.getText() === 'element',
  );
  if (
    attribute?.initializer === undefined ||
    !ts.isJsxExpression(attribute.initializer) ||
    attribute.initializer.expression === undefined
  ) {
    return '';
  }
  return attribute.initializer.expression.getText();
}

function childRoutes(node: ts.JsxElement): Array<ts.JsxElement | ts.JsxSelfClosingElement> {
  return node.children.filter(
    (child): child is ts.JsxElement | ts.JsxSelfClosingElement =>
      (ts.isJsxElement(child) && tagName(child.openingElement) === 'Route') ||
      (ts.isJsxSelfClosingElement(child) && tagName(child) === 'Route'),
  );
}

function joinPattern(parent: string, path: string | undefined, index: boolean): string {
  if (index || path === undefined) return parent;
  if (path === '*') return '*';
  if (path.startsWith('/')) return path;
  return parent === '/' ? `/${path}` : `${parent}/${path}`;
}

function componentName(element: string): string {
  const match = /^<([A-Za-z][\w.]*)/.exec(element);
  if (match === null) throw new Error(`Route element is not a JSX component: ${element}`);
  return match[1];
}

function routeKind(pattern: string, element: string): AppRouteKind {
  if (pattern === '*') return 'not_found';
  if (element.startsWith('<Navigate')) return 'redirect';
  if (element.startsWith('<WorkspaceIssueByIdentifierRedirect')) return 'resolver_redirect';
  return 'page';
}

function extractAppRoutes(): readonly ExtractedRoute[] {
  const source = ts.createSourceFile(
    'App.tsx',
    APP_SOURCE,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
  let routesRoot: ts.JsxElement | undefined;
  const findRoutes = (node: ts.Node): void => {
    if (routesRoot !== undefined) return;
    if (ts.isJsxElement(node) && tagName(node.openingElement) === 'Routes') {
      routesRoot = node;
      return;
    }
    ts.forEachChild(node, findRoutes);
  };
  findRoutes(source);
  if (routesRoot === undefined) throw new Error('App.tsx does not contain <Routes>');

  const result: ExtractedRoute[] = [];
  const visit = (
    route: ts.JsxElement | ts.JsxSelfClosingElement,
    parentPattern: string,
    inheritedProtected: boolean,
  ): void => {
    const opening = ts.isJsxElement(route) ? route.openingElement : route;
    const element = elementAttribute(opening);
    const protectedRoute = inheritedProtected || element.startsWith('<RequireAuth');
    const pattern = joinPattern(
      parentPattern,
      stringAttribute(opening, 'path'),
      hasAttribute(opening, 'index'),
    );
    const children = ts.isJsxElement(route) ? childRoutes(route) : [];
    if (children.length > 0) {
      children.forEach((child) => visit(child, pattern, protectedRoute));
      return;
    }
    const kind = routeKind(pattern, element);
    const redirectTo = kind === 'redirect' ? stringAttributeFromElement(element, 'to') : undefined;
    result.push({
      pattern,
      element: componentName(element),
      access: protectedRoute ? 'protected' : 'public',
      kind,
      ...(redirectTo === undefined ? {} : { redirectTo }),
    });
  };
  childRoutes(routesRoot).forEach((route) => visit(route, '/', false));
  return result;
}

function stringAttributeFromElement(element: string, name: string): string | undefined {
  const match = new RegExp(`\\b${name}=(?:"([^"]+)"|'([^']+)')`).exec(element);
  return match?.[1] ?? match?.[2];
}

describe('App route manifest (fail closed)', () => {
  it('accounts for every App.tsx leaf route, guard and redirect semantic', () => {
    const actual = extractAppRoutes();
    const declared = APP_ROUTE_MANIFEST.map(({ pattern, element, access, kind, redirectTo }) => ({
      pattern,
      element,
      access,
      kind,
      ...(redirectTo === undefined ? {} : { redirectTo }),
    }));
    const byPattern = (left: ExtractedRoute, right: ExtractedRoute): number =>
      left.pattern.localeCompare(right.pattern) || left.access.localeCompare(right.access);
    expect([...declared].sort(byPattern)).toEqual([...actual].sort(byPattern));
  });

  it('uses unique IDs/access-qualified patterns and a matching concrete sample for every route', () => {
    expect(new Set(APP_ROUTE_MANIFEST.map(({ id }) => id)).size).toBe(APP_ROUTE_MANIFEST.length);
    expect(
      new Set(APP_ROUTE_MANIFEST.map(({ pattern, access }) => `${access}:${pattern}`)).size,
    ).toBe(APP_ROUTE_MANIFEST.length);
    for (const route of APP_ROUTE_MANIFEST) {
      expect(
        matchPath(
          { path: route.pattern, end: true },
          new URL(route.samplePath, 'http://route-manifest.local').pathname,
        ),
        `${route.id}: ${route.samplePath} must match ${route.pattern}`,
      ).not.toBeNull();
    }
  });

  it('declares permission and browser evidence truthfully for every route', () => {
    for (const route of APP_ROUTE_MANIFEST) {
      expect(route.permission).toBe(
        route.pattern === '/w/:workspaceSlug/settings/danger'
          ? 'workspace_owner'
          : route.pattern.startsWith('/w/:workspaceSlug/settings')
            ? 'workspace_admin'
            : route.pattern === '/approvals' || route.pattern === '/w/:workspaceSlug/approvals'
              ? 'human'
              : route.access === 'protected'
                ? 'authenticated'
                : 'anonymous',
      );
      if (route.kind === 'redirect') {
        expect(route.browser.level).toBe('redirect');
      } else if (route.kind === 'resolver_redirect') {
        expect(route.browser.level).toBe('extended');
        if (route.browser.level === 'extended') {
          expect(route.browser.expectedPath).toMatch(/^\//);
        }
      }
    }
    expect(
      APP_ROUTE_MANIFEST.filter(
        (route) => route.kind !== 'redirect' && !['core', 'extended'].includes(route.browser.level),
      ),
      'every normal route must have executable core or extended browser evidence',
    ).toEqual([]);
  });
});
