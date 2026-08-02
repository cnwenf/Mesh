import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, relative, sep } from 'node:path';

export function collectApiStepTitles(steps) {
  const titles = [];
  for (const step of Array.isArray(steps) ? steps : []) {
    if (['pw:api', 'expect'].includes(step?.category) && typeof step.title === 'string') {
      titles.push(step.title);
    }
    titles.push(...collectApiStepTitles(step?.steps));
  }
  return titles;
}

export function collectScreenshotOutputs(attachments) {
  const manifests = (Array.isArray(attachments) ? attachments : []).filter(
    (attachment) => attachment?.name === 'mes108-screenshot-outputs',
  );
  if (manifests.length === 0) return [];
  if (manifests.length !== 1) {
    throw new Error('Playwright evidence test must attach one screenshot path manifest');
  }
  const [manifest] = manifests;
  if (manifest.contentType !== 'application/json' || manifest.body === undefined) {
    throw new Error('Playwright screenshot path manifest must be inline JSON');
  }
  let outputs;
  try {
    outputs = JSON.parse(Buffer.from(manifest.body).toString('utf8'));
  } catch {
    throw new Error('Playwright screenshot path manifest is malformed');
  }
  if (
    !Array.isArray(outputs) ||
    outputs.some(
      (output) =>
        typeof output?.path !== 'string' ||
        output.path.length === 0 ||
        output.path.startsWith('/') ||
        output.path.split(/[\\/]/u).includes('..') ||
        !/^[0-9a-f]{64}$/u.test(output.sha256 ?? ''),
    ) ||
    new Set(outputs.map((output) => output.path)).size !== outputs.length
  ) {
    throw new Error('Playwright screenshot path manifest contains invalid paths');
  }
  return outputs;
}

export default class Mes108PlaywrightReporter {
  constructor(options = {}) {
    this.outputPath = options.outputPath ?? process.env.MES108_PLAYWRIGHT_REPORT;
    this.cwd = options.cwd ?? process.cwd();
    this.tests = [];
  }

  onTestEnd(test, result) {
    const project = typeof test?.parent?.project === 'function' ? test.parent.project() : undefined;
    const use = project?.use;
    this.tests.push({
      spec: relative(this.cwd, test.location.file).split(sep).join('/'),
      testTitle: test.title,
      project: project?.name,
      status: result.status,
      expectedStatus: test.expectedStatus,
      apiSteps: collectApiStepTitles(result.steps),
      screenshotOutputs: collectScreenshotOutputs(result.attachments),
      environment: {
        browserName: use?.browserName,
        locale: use?.locale,
        timezoneId: use?.timezoneId,
        deviceScaleFactor: use?.deviceScaleFactor,
        viewport: use?.viewport,
      },
    });
  }

  onEnd(result) {
    if (typeof this.outputPath !== 'string' || this.outputPath.length === 0) {
      throw new Error('MES108_PLAYWRIGHT_REPORT is required');
    }
    mkdirSync(dirname(this.outputPath), { recursive: true });
    writeFileSync(
      this.outputPath,
      JSON.stringify({ schemaVersion: 1, status: result.status, tests: this.tests }),
      'utf8',
    );
  }
}
