#!/bin/sh
set -eu
umask 077

if [ "$(uname -s)" != Darwin ]; then
  echo "cync-cli's Bluetooth helper requires macOS." >&2
  exit 1
fi

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python=${PYTHON:-python3}
app_support=${CYNC_HOME:-"${HOME:?HOME is not set}/Library/Application Support/cync-cli"}
bin_dir=${CYNC_CLI_BIN_DIR:-"${HOME:?HOME is not set}/.local/bin"}

if ! "$python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 or newer is required (selected: $python)." >&2
  exit 1
fi
if [ ! -f "$project_dir/native/main.swift" ]; then
  echo "Missing native/main.swift; this checkout is incomplete." >&2
  exit 1
fi

mkdir -p "$app_support" "$bin_dir" "$app_support/lib" "$app_support/share/cync-cli/native"
chmod 700 "$app_support" "$app_support/lib" "$app_support/share" "$app_support/share/cync-cli" "$app_support/share/cync-cli/native"
"$python" -m venv --without-pip "$app_support/venv"

package_dir="$app_support/lib/cync_cli"
staged_package="$app_support/lib/cync_cli.new.$$"
rm -rf "$staged_package"
mkdir -p "$staged_package"
cp -R "$project_dir/src/cync_cli/." "$staged_package/"
rm -rf "$package_dir"
mv "$staged_package" "$package_dir"
cp "$project_dir/native/main.swift" "$app_support/share/cync-cli/native/main.swift"

cat > "$bin_dir/cync" <<'LAUNCHER'
#!/bin/sh
set -eu
app_support=${CYNC_HOME:-"${HOME:?HOME is not set}/Library/Application Support/cync-cli"}
export PYTHONPATH="$app_support/lib${PYTHONPATH:+:$PYTHONPATH}"
exec "$app_support/venv/bin/python" -m cync_cli "$@"
LAUNCHER
chmod 755 "$bin_dir/cync"

echo "Installed cync to $bin_dir/cync"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "Add $bin_dir to PATH to run it from any directory." ;;
esac
