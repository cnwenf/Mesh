import {
  chmodSync,
  copyFileSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const MES161_PORT_ENV = 'MES161_FRONTEND_PORT';

describe('MES-161 Playwright configuration', () => {
  const originalPort = process.env[MES161_PORT_ENV];

  afterEach(() => {
    if (originalPort === undefined) delete process.env[MES161_PORT_ENV];
    else process.env[MES161_PORT_ENV] = originalPort;
    vi.resetModules();
  });

  it('uses a valid configured frontend port', async () => {
    process.env[MES161_PORT_ENV] = '19322';
    vi.resetModules();

    const { default: config } = await import('../playwright.mes161.config');

    expect(config.use?.baseURL).toBe('http://127.0.0.1:19322');
  });

  it.each(['', '0', '65536', '1.5', 'not-a-port'])(
    'fails fast for invalid frontend port %j',
    async (port) => {
      process.env[MES161_PORT_ENV] = port;
      vi.resetModules();

      await expect(import('../playwright.mes161.config')).rejects.toThrow(
        'MES161_FRONTEND_PORT must be an integer TCP port',
      );
    },
  );
});

describe('mock e2e endpoint wiring', () => {
  const originalMockPort = process.env.MESH_E2E_MOCK_PORT;
  const originalMockBase = process.env.MESH_MOCK_BASE;

  afterEach(() => {
    if (originalMockPort === undefined) delete process.env.MESH_E2E_MOCK_PORT;
    else process.env.MESH_E2E_MOCK_PORT = originalMockPort;
    if (originalMockBase === undefined) delete process.env.MESH_MOCK_BASE;
    else process.env.MESH_MOCK_BASE = originalMockBase;
    vi.resetModules();
  });

  it('keeps helpers, the mock server, and the browser build on one configured port', async () => {
    process.env.MESH_E2E_MOCK_PORT = '19323';
    // A stale legacy value must not split helper traffic from the server that
    // the Playwright config starts for this run.
    process.env.MESH_MOCK_BASE = 'http://127.0.0.1:19999';
    vi.resetModules();

    const helpersUrl = pathToFileURL(path.resolve(process.cwd(), 'e2e/helpers.ts')).href;
    const [{ MOCK_BASE }, { default: config }] = await Promise.all([
      import(/* @vite-ignore */ helpersUrl),
      import('../playwright.config'),
    ]);
    const webServers = Array.isArray(config.webServer) ? config.webServer : [config.webServer];

    expect(MOCK_BASE).toBe('http://127.0.0.1:19323');
    expect(webServers[0]?.url).toBe('http://127.0.0.1:19323/healthz');
    expect(webServers[0]?.env).toMatchObject({ MESH_MOCK_PORT: '19323' });
    expect(webServers[1]?.env).toMatchObject({
      VITE_MESH_API_BASE_URL: 'http://127.0.0.1:19323',
      VITE_MESH_WS_BASE_URL: 'ws://127.0.0.1:19323',
    });
  });
});

describe('MES-128/MES-161 real-stack runner', () => {
  function runWithStackPort(
    stackPort: string,
    options: { shellPort?: string; extraStackEnv?: string } = {},
  ): {
    log: string;
    status: number | null;
    stderr: string;
  } {
    const fixtureRoot = mkdtempSync(path.join(tmpdir(), 'mesh-e2e-runner-'));
    const runnerDir = path.join(fixtureRoot, 'frontend/e2e/mes128-real');
    const binDir = path.join(fixtureRoot, 'bin');
    const logPath = path.join(fixtureRoot, 'calls.log');
    mkdirSync(runnerDir, { recursive: true });
    mkdirSync(binDir, { recursive: true });
    writeFileSync(logPath, '');

    copyFileSync(
      path.resolve(process.cwd(), 'e2e/mes128-real/run-e2e.sh'),
      path.join(runnerDir, 'run-e2e.sh'),
    );
    writeFileSync(
      path.join(runnerDir, 'stack.env'),
      `MESH_FRONTEND_PORT=${stackPort}\n${options.extraStackEnv ?? ''}`,
    );

    const writeStub = (name: string, body: string): void => {
      const target = path.join(binDir, name);
      writeFileSync(target, `#!/usr/bin/env bash\nset -euo pipefail\n${body}\n`);
      chmodSync(target, 0o755);
    };
    writeStub(
      'docker',
      'printf \'docker frontend=%s args=%s\\n\' "${MESH_FRONTEND_PORT-<unset>}" "$*" >> "${CALL_LOG}"',
    );
    writeStub('curl', 'printf \'curl %s\\n\' "$*" >> "${CALL_LOG}"');
    writeStub(
      'npx',
      'printf \'npx mes128=%s mes161=%s args=%s\\n\' "${MES128_FRONTEND_PORT:-}" "${MES161_FRONTEND_PORT:-}" "$*" >> "${CALL_LOG}"',
    );

    const env: NodeJS.ProcessEnv = {
      ...process.env,
      PATH: `${binDir}:${process.env.PATH ?? ''}`,
      CALL_LOG: logPath,
    };
    delete env.MESH_FRONTEND_PORT;
    delete env.MES128_FRONTEND_PORT;
    delete env.MES161_FRONTEND_PORT;
    if (options.shellPort !== undefined) env.MESH_FRONTEND_PORT = options.shellPort;

    try {
      const result = spawnSync('bash', [path.join(runnerDir, 'run-e2e.sh')], {
        cwd: fixtureRoot,
        env,
        encoding: 'utf8',
      });
      return {
        log: readFileSync(logPath, 'utf8'),
        status: result.status,
        stderr: result.stderr,
      };
    } finally {
      rmSync(fixtureRoot, { recursive: true, force: true });
    }
  }

  it('uses the stack frontend port for readiness and both Playwright suites', () => {
    const result = runWithStackPort('19321');

    expect(result.status).toBe(0);
    expect(result.log).toContain('http://127.0.0.1:19321/readyz');
    expect(result.log).toContain('npx mes128=19321 mes161=');
    expect(result.log).toContain('npx mes128= mes161=19321');
    expect(result.log).toMatch(/docker frontend=19321 args=.* up -d --build/);
    expect(result.log).not.toContain('18430');
  });

  it('normalizes an empty shell override before Compose and both Playwright suites', () => {
    const result = runWithStackPort('19321', { shellPort: '' });

    expect(result.status).toBe(0);
    expect(result.log).toMatch(/docker frontend=19321 args=.* up -d --build/);
    expect(result.log).toContain('http://127.0.0.1:19321/readyz');
    expect(result.log).toContain('npx mes128=19321 mes161=');
    expect(result.log).toContain('npx mes128= mes161=19321');
  });

  it('uses a valid shell override for Compose, readiness, and both Playwright suites', () => {
    const result = runWithStackPort('19321', { shellPort: '19324' });

    expect(result.status).toBe(0);
    expect(result.log).toMatch(/docker frontend=19324 args=.* up -d --build/);
    expect(result.log).toContain('http://127.0.0.1:19324/readyz');
    expect(result.log).toContain('npx mes128=19324 mes161=');
    expect(result.log).toContain('npx mes128= mes161=19324');
  });

  it.each(['0', '65536', '18446744073709551617', 'not-a-port'])(
    'fails before starting the stack when its frontend port is invalid: %s',
    (port) => {
      const result = runWithStackPort(port);

      expect(result.status).not.toBe(0);
      expect(result.stderr).toContain('MESH_FRONTEND_PORT must be an integer TCP port');
      expect(result.log).not.toContain('up -d --build');
    },
  );

  it('rejects duplicate stack frontend port entries even when the first value is empty', () => {
    const result = runWithStackPort('', {
      extraStackEnv: 'MESH_FRONTEND_PORT=19321\n',
    });

    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain('MESH_FRONTEND_PORT must appear exactly once');
    expect(result.log).not.toContain('up -d --build');
  });
});
