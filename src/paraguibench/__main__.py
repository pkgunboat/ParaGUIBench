"""允许通过 ``python -m paraguibench`` 调用安全 CLI。"""

from paraguibench.cli.main import main

raise SystemExit(main())
