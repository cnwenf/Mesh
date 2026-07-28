/**
 * 测试辅助导出:为 api.test.ts 提供 api 层函数 + 一个频道前缀常量
 * (避免 api.test 内重复 re-export 样板)。
 */
export {
  createExportJob,
  createImportJob,
  downloadDataJobProduct,
  getDataJob,
  listDataJobs,
  runImportJob,
  validateImportJob,
} from '../api';

export function dataJobChannelExportHelper(): string {
  return 'data_job';
}
