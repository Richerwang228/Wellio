import type { CapacitorConfig } from '@capacitor/cli'
import { KeyboardResize, KeyboardStyle } from '@capacitor/keyboard'

// Simulator demo only: URL is supplied by the launcher, never baked into source.
const address = process.env.WELLIO_NATIVE_URL
const target = address ? new URL(address) : undefined
if (target && (!['http:', 'https:'].includes(target.protocol) || target.username || target.password)) {
  throw new Error('WELLIO_NATIVE_URL must be an HTTP(S) address without credentials.')
}
const config: CapacitorConfig = {
  appId: 'app.wellio.demo',
  appName: 'Wellio',
  webDir: 'native-shell',
  backgroundColor: '#FAF9F2',
  ios: { contentInset: 'never', preferredContentMode: 'mobile', backgroundColor: '#FAF9F2' },
  ...(target ? { server: { url: target.href, cleartext: target.protocol === 'http:' } } : {}),
  plugins: { Keyboard: { resize: KeyboardResize.Native, style: KeyboardStyle.Light } },
}
export default config
