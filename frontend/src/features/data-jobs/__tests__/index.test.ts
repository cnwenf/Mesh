import { describe, expect, it } from 'vitest';
import {
  applyDataJobFrame,
  createExportJob,
  createImportJob,
  dataJobChannel,
  getDataJob,
  listDataJobs,
} from '../index';

describe('data-jobs public surface', () => {
  it('exports the supported API, realtime reducer and type helpers from one entrypoint', () => {
    expect(createExportJob).toBeTypeOf('function');
    expect(createImportJob).toBeTypeOf('function');
    expect(getDataJob).toBeTypeOf('function');
    expect(listDataJobs).toBeTypeOf('function');
    expect(applyDataJobFrame).toBeTypeOf('function');
    expect(dataJobChannel('job-1')).toBe('data_job:job-1');
  });
});
