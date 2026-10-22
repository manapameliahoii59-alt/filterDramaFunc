"""
stealth.py
完全独立的常读平台浏览器拟真与反风控配置
（从成熟工业级规范提炼，抹除自动化特征，防止触发常读安全 SDK）
"""
import urllib.parse
from typing import Any, Dict

CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SEC_CH_UA = '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'
SEC_CH_UA_MOBILE = "?0"
SEC_CH_UA_PLATFORM = '"Windows"'

BROWSER_LOCALE = "zh-CN"
BROWSER_TIMEZONE = "Asia/Shanghai"
BROWSER_VIEWPORT = {"width": 1440, "height": 900}
ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7"

HOME_ORIGIN = "https://www.changdupingtai.com"
HOME_URL = f"{HOME_ORIGIN}/page/home?show=true"
SHORT_PLAY_LIST_URL = f"{HOME_ORIGIN}/sale/short-play/list"

# 高拟真 Stealth 注入脚本：
# 1. 在原型链层清除 navigator.webdriver；
# 2. 补全真实的 window.chrome 原生对象结构（含 [native code] 函数签名）；
# 3. 补全常见插件数组 navigator.plugins；
# 4. 遮蔽 WebGL SwiftShader，模拟主流显卡特征。
STEALTH_INIT_SCRIPT = """
(function() {
  // 1. 原型链 webdriver 彻底清除
  try {
    const proto = Object.getPrototypeOf(navigator);
    if ('webdriver' in proto) {
      delete proto.webdriver;
    }
  } catch (e) {}
  try {
    delete navigator.webdriver;
  } catch (e) {}

  // 2. 补齐真实的 window.chrome 对象与 [native code] 原生函数特征
  if (!window.chrome) {
    window.chrome = {};
  }
  const makeNativeString = function(fnName) {
    return 'function ' + fnName + '() { [native code] }';
  };
  const dummyFn = function(name) {
    const fn = function() {};
    fn.toString = function() { return makeNativeString(name); };
    return fn;
  };
  if (!window.chrome.app) {
    window.chrome.app = {
      isInstalled: false,
      InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
      RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
      getDetails: dummyFn('getDetails'),
      getIsInstalled: dummyFn('getIsInstalled'),
      installState: dummyFn('installState'),
      runningState: dummyFn('runningState'),
    };
  }
  if (!window.chrome.csi) {
    window.chrome.csi = dummyFn('csi');
  }
  if (!window.chrome.loadTimes) {
    window.chrome.loadTimes = dummyFn('loadTimes');
  }
  if (!window.chrome.runtime) {
    window.chrome.runtime = {
      OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
      OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
      PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
      PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
      PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
      RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
      connect: dummyFn('connect'),
      sendMessage: dummyFn('sendMessage'),
    };
  }

  // 3. 补全常见插件与 MIME 类型数组，严格匹配原生 PluginArray / MimeTypeArray 原型链（防范 shelldriver 检测）
  if (!navigator.plugins || navigator.plugins.length === 0) {
    const pluginList = [
      { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    ];
    pluginList.forEach(function(p) {
      if (typeof Plugin !== 'undefined') {
        try { Object.setPrototypeOf(p, Plugin.prototype); } catch (e) {}
      }
    });
    const pArr = [...pluginList];
    if (typeof PluginArray !== 'undefined') {
      try { Object.setPrototypeOf(pArr, PluginArray.prototype); } catch (e) {}
    }
    pArr.item = function(i) { return this[i] || null; };
    pArr.namedItem = function(name) {
      for (let i = 0; i < this.length; i++) {
        if (this[i] && this[i].name === name) return this[i];
      }
      return null;
    };
    pArr.refresh = function() {};

    Object.defineProperty(navigator, 'plugins', {
      get: function() { return pArr; },
      enumerable: true,
      configurable: true,
    });
  }

  if (!navigator.mimeTypes || navigator.mimeTypes.length === 0) {
    const mimeList = [
      { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
      { type: 'text/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
    ];
    mimeList.forEach(function(m) {
      if (typeof MimeType !== 'undefined') {
        try { Object.setPrototypeOf(m, MimeType.prototype); } catch (e) {}
      }
    });
    const mArr = [...mimeList];
    if (typeof MimeTypeArray !== 'undefined') {
      try { Object.setPrototypeOf(mArr, MimeTypeArray.prototype); } catch (e) {}
    }
    mArr.item = function(i) { return this[i] || null; };
    mArr.namedItem = function(name) {
      for (let i = 0; i < this.length; i++) {
        if (this[i] && this[i].type === name) return this[i];
      }
      return null;
    };

    Object.defineProperty(navigator, 'mimeTypes', {
      get: function() { return mArr; },
      enumerable: true,
      configurable: true,
    });
  }

  // 4. 遮蔽 WebGL SwiftShader，模拟主流显卡特征
  try {
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
      if (param === 37445) return 'Google Inc. (Intel)';
      if (param === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)';
      return getParameter.apply(this, arguments);
    };
    if (typeof WebGL2RenderingContext !== 'undefined') {
      const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
      WebGL2RenderingContext.prototype.getParameter = function(param) {
        if (param === 37445) return 'Google Inc. (Intel)';
        if (param === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)';
        return getParameter2.apply(this, arguments);
      };
    }
  } catch (e) {}

  Object.defineProperty(navigator, 'languages', {
    get: function() { return Object.freeze(['zh-CN', 'zh', 'en-US', 'en']); },
  });
})();
"""

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--autoplay-policy=no-user-gesture-required",
]


def browser_context_kwargs(storage_state: str | None = None) -> Dict[str, Any]:
    """生成 Playwright 浏览器上下文参数"""
    kwargs: Dict[str, Any] = {
        "user_agent": CHROME_USER_AGENT,
        "locale": BROWSER_LOCALE,
        "timezone_id": BROWSER_TIMEZONE,
        "viewport": dict(BROWSER_VIEWPORT),
        "color_scheme": "light",
        "extra_http_headers": {
            "Accept-Language": ACCEPT_LANGUAGE,
            "sec-ch-ua": SEC_CH_UA,
            "sec-ch-ua-mobile": SEC_CH_UA_MOBILE,
            "sec-ch-ua-platform": SEC_CH_UA_PLATFORM,
        },
    }
    if storage_state:
        kwargs["storage_state"] = storage_state
    return kwargs


def launch_playwright_browser(pw, headless: bool = True, extra_args: list | None = None):
    """
    自适应启动浏览器：优先启动系统原生 Google Chrome（拟真度与反爬最强），
    若无 Chrome 则自适应回退到已安装的 Chromium 或 Edge。
    """
    import os
    from pathlib import Path
    args = list(LAUNCH_ARGS) + (extra_args or [])

    # 1. 优先使用系统原生 Google Chrome
    try:
        return pw.chromium.launch(channel="chrome", headless=headless, args=args)
    except Exception:
        pass

    # 2. 尝试系统已下载的 Chromium 目录
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        chromes = sorted(Path(local_app_data).glob("ms-playwright/chromium-*/chrome-win64/chrome.exe"))
        if chromes:
            try:
                return pw.chromium.launch(executable_path=str(chromes[-1]), headless=headless, args=args)
            except Exception:
                pass

    # 3. 尝试 Edge
    try:
        return pw.chromium.launch(channel="msedge", headless=headless, args=args)
    except Exception:
        pass

    # 4. 默认 Chromium
    return pw.chromium.launch(headless=headless, args=args)
