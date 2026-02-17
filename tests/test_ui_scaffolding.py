from __future__ import annotations

import json
from pathlib import Path


def _read_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def test_ui_package_has_typescript_and_quality_scripts() -> None:
    payload = _read_json('ui/package.json')

    scripts = payload.get('scripts', {})
    assert scripts.get('dev') == 'vite'
    assert scripts.get('build') == 'tsc -b && vite build'
    assert scripts.get('test') == 'vitest run'
    assert scripts.get('typecheck') == 'tsc --noEmit'

    dev_deps = payload.get('devDependencies', {})
    assert 'typescript' in dev_deps
    assert 'vitest' in dev_deps
    assert '@testing-library/react' in dev_deps


def test_ui_uses_ts_entrypoints_and_strict_config() -> None:
    index_html = Path('ui/index.html').read_text(encoding='utf-8')
    assert '/src/main.tsx' in index_html

    assert Path('ui/src/App.tsx').exists()
    assert Path('ui/src/main.tsx').exists()
    assert not Path('ui/src/App.jsx').exists()
    assert not Path('ui/src/main.jsx').exists()

    tsconfig = _read_json('ui/tsconfig.json')
    compiler_options = tsconfig.get('compilerOptions', {})
    assert compiler_options.get('strict') is True
    assert compiler_options.get('noUnusedLocals') is True
    assert compiler_options.get('noUnusedParameters') is True


def test_ui_has_frontend_unit_tests_for_api_and_rendering() -> None:
    api_test = Path('ui/src/__tests__/api.test.ts')
    app_test = Path('ui/src/__tests__/app.test.tsx')
    assert api_test.exists()
    assert app_test.exists()

    api_source = api_test.read_text(encoding='utf-8')
    app_source = app_test.read_text(encoding='utf-8')

    assert 'fetchRuns' in api_source
    assert 'fetchQueue' in api_source
    assert 'getWsRoot' in api_source
    assert 'MockWebSocket' in app_source
    assert 'Live activity connected' in app_source


def test_single_command_ui_launcher_exists() -> None:
    launcher = Path('scripts/run-ui.sh')
    assert launcher.exists()
    text = launcher.read_text(encoding='utf-8')
    assert 'npm run dev' in text
