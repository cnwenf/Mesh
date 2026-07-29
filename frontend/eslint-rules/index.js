/**
 * Mesh 本地 ESLint 规则插件(flat config 经 eslint.config.js 的 plugins 注册,不发包)。
 */
import { noHardcodedColors } from './no-hardcoded-colors.js';

export default {
  rules: {
    'no-hardcoded-colors': noHardcodedColors,
  },
};
