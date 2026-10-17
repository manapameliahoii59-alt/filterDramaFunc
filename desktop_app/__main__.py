"""允许 `python -m desktop_app` 直接启动。"""

import sys

from desktop_app.app import main

if __name__ == "__main__":
    sys.exit(main())
