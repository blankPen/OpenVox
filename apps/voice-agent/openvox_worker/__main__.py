"""Allow ``python -m openvox_worker`` to invoke the CLI."""
from .cli import main

raise SystemExit(main())
