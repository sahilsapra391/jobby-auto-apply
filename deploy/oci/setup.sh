#!/usr/bin/env bash
set -euo pipefail

repo="${1:-$HOME/jobby-auto-apply}"
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is for the dedicated OCI Linux worker." >&2
  exit 2
fi
if [[ ! -f "$repo/scripts/document_worker.py" ]]; then
  echo "Clone the repository to $repo first, or pass its absolute path." >&2
  exit 2
fi

require_disk_space() {
  local available_kb
  available_kb="$(df -Pk / | awk 'NR == 2 {print $4}')"
  if [[ ! "$available_kb" =~ ^[0-9]+$ ]] || (( available_kb < 6291456 )); then
    echo "The root volume needs at least 6 GB free before worker setup." >&2
    exit 2
  fi
}

ensure_swap() {
  local swap_file="/swapfile"
  if sudo swapon --noheadings --show=NAME 2>/dev/null | grep -Fxq "$swap_file"; then
    local swap_bytes
    swap_bytes="$(sudo swapon --bytes --noheadings --show=SIZE "$swap_file" 2>/dev/null | tr -d '[:space:]')"
    if [[ ! "$swap_bytes" =~ ^[0-9]+$ ]] || (( swap_bytes < 2147483648 )); then
      echo "The active $swap_file is smaller than 2 GB. Refusing the 1 GB worker setup." >&2
      exit 2
    else
      echo "At least 2 GB of swap is already active."
    fi
  elif [[ -e "$swap_file" ]]; then
    if [[ "$(sudo blkid -p -o value -s TYPE "$swap_file" 2>/dev/null || true)" != "swap" ]]; then
      echo "Leaving existing $swap_file untouched because it is not a swap file." >&2
      exit 2
    fi
    local existing_swap_bytes
    existing_swap_bytes="$(sudo stat -c %s "$swap_file" 2>/dev/null || true)"
    if [[ ! "$existing_swap_bytes" =~ ^[0-9]+$ ]] || (( existing_swap_bytes < 2147483648 )); then
      echo "The existing $swap_file is smaller than 2 GB. Refusing the 1 GB worker setup." >&2
      exit 2
    fi
    sudo chmod 600 "$swap_file"
    sudo swapon "$swap_file"
  else
    sudo fallocate -l 2G "$swap_file"
    sudo chmod 600 "$swap_file"
    sudo mkswap "$swap_file"
    sudo swapon "$swap_file"
  fi

  if ! sudo awk '$1 == "/swapfile" && $3 == "swap" {found=1} END {exit !found}' \
      /etc/fstab; then
    printf '%s\n' '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  fi
  printf '%s\n' 'vm.swappiness=20' | \
    sudo tee /etc/sysctl.d/60-jobby-swap.conf >/dev/null
  sudo sysctl -q -p /etc/sysctl.d/60-jobby-swap.conf

  if ! sudo swapon --noheadings --show=NAME 2>/dev/null | grep -Fxq "$swap_file"; then
    echo "$swap_file is not active. The 1 GB worker cannot be installed safely." >&2
    exit 2
  fi
}

require_disk_space
ensure_swap

sudo apt-get update
sudo apt-get install -y \
  bubblewrap python3-venv python3-pip poppler-utils fonts-crosextra-carlito \
  fonts-crosextra-caladea git curl ca-certificates xz-utils

verify_bubblewrap() {
  local arguments=(
    --die-with-parent
    --unshare-all
    --share-net
    --ro-bind /usr /usr
    --symlink usr/bin /bin
    --symlink usr/sbin /sbin
    --symlink usr/lib /lib
    --proc /proc
    --dev /dev
  )
  if [[ -e /usr/lib64 ]]; then
    arguments+=(--symlink usr/lib64 /lib64)
  fi
  if ! bwrap "${arguments[@]}" -- /usr/bin/true; then
    echo "Bubblewrap user namespaces are unavailable. Refusing an unsandboxed worker." >&2
    exit 2
  fi
}

verify_bubblewrap

install_node() {
  local current_major=0
  local node_arch
  local node_archive
  local node_stage
  if command -v node >/dev/null 2>&1; then
    current_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
  fi
  if [[ "$current_major" =~ ^[0-9]+$ ]] && (( current_major >= 22 )); then
    return 0
  fi
  case "$(dpkg --print-architecture)" in
    amd64) node_arch="x64" ;;
    arm64) node_arch="arm64" ;;
    *) echo "Node.js installation requires amd64 or arm64." >&2; exit 2 ;;
  esac
  node_stage="$(mktemp -d)"
  curl --fail --location --silent --show-error \
    --output "$node_stage/SHASUMS256.txt" \
    https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt
  node_archive="$(awk -v suffix="linux-${node_arch}.tar.xz" \
    '$2 ~ suffix "$" {print $2; exit}' "$node_stage/SHASUMS256.txt")"
  if [[ ! "$node_archive" =~ ^node-v[0-9]+\.[0-9]+\.[0-9]+-linux-(x64|arm64)\.tar\.xz$ ]]; then
    echo "Node.js release metadata did not contain the expected archive." >&2
    exit 2
  fi
  curl --fail --location --silent --show-error \
    --output "$node_stage/$node_archive" \
    "https://nodejs.org/dist/latest-v22.x/$node_archive"
  (cd "$node_stage" && sha256sum --check --ignore-missing SHASUMS256.txt)
  sudo tar --extract --xz --file "$node_stage/$node_archive" \
    --directory /usr/local --strip-components=1
  rm -rf -- "$node_stage"
}

install_node

install_google_chrome() {
  if command -v google-chrome >/dev/null 2>&1; then
    return 0
  fi
  if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then
    echo "Google Chrome installation currently requires an amd64 worker." >&2
    exit 2
  fi
  local chrome_package
  chrome_package="$(mktemp --suffix=.deb)"
  curl --fail --location --silent --show-error \
    --output "$chrome_package" \
    https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  sudo apt-get install -y "$chrome_package"
  rm -f "$chrome_package"
}

install_google_chrome

mkdir -p "$HOME/.local/bin"
npm config set prefix "$HOME/.local"
PATH="$HOME/.local/bin:/usr/local/bin:$PATH" npm install -g \
  @openai/codex@0.149.0 \
  @anthropic-ai/claude-code@2.1.238 \
  @google/gemini-cli@0.56.0
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
for provider_cli in codex claude gemini; do
  if ! command -v "$provider_cli" >/dev/null 2>&1; then
    echo "$provider_cli was not installed in $HOME/.local/bin." >&2
    exit 2
  fi
  "$provider_cli" --version
done

python3 -m venv "$repo/.venv"
"$repo/.venv/bin/python" -m pip install --upgrade pip
"$repo/.venv/bin/python" -m pip install pymupdf pdfplumber
chmod 755 "$repo/scripts/document_worker.py"

mkdir -p "$HOME/.job-application" "$HOME/.config/systemd/user" \
  "$HOME/.codex" "$HOME/.claude" "$HOME/.gemini"
chmod 700 "$HOME/.job-application" "$HOME/.codex" "$HOME/.claude" "$HOME/.gemini"
if [[ ! -f "$HOME/.job-application/config.env" ]]; then
  install -m 600 "$repo/deploy/oci/config.env.example" \
    "$HOME/.job-application/config.env"
  echo "Created ~/.job-application/config.env. Fill its four required values."
fi
chmod 600 "$HOME/.job-application/config.env"

unit="$HOME/.config/systemd/user/jobby-documents.service"
sed "s|%h/jobby-auto-apply|$repo|g" \
  "$repo/deploy/oci/jobby-documents.service" > "$unit"
chmod 600 "$unit"
systemctl --user daemon-reload

echo "Authenticate each provider CLI you want this worker to use."
echo "Credential JSON files must remain owner-only, mode 600."
echo "Sign in to a provider CLI when you are ready, then use Jobby Settings to connect it."
echo "Settings sends one shared connection-order key to the Mac and this worker."
echo "Then run:"
echo "  systemctl --user enable --now jobby-documents"
echo "To keep the user service running after SSH disconnects:"
echo "  sudo loginctl enable-linger $USER"
