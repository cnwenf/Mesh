/** 独立注册入口：复用认证公共流程，默认展示注册表单。 */
import { LoginPage } from './LoginPage';

export function RegisterPage(): React.JSX.Element {
  return <LoginPage initialMode="register" />;
}
