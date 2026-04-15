uv run pyinstaller UIWhisperCPP.spec --noconfirm
codesign --force --deep --sign - dist/UIWhisperCPP.app
codesign --verify --deep --strict --verbose=2 dist/UIWhisperCPP.app
open dist/UIWhisperCPP.app
