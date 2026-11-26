#!/usr/bin/env bash
# Publish a new poketerm release that users get via `poketerm --update`.
#
#   ./release.sh 1.0.0            stable release (from main) - everyone is offered it
#   ./release.sh 1.0.0-beta.1     pre-release - only `poketerm --update --beta` gets it
#   ./release.sh 1.0.0 --dry-run  run all checks, change nothing
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

usage() {
    sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
    echo ""
    echo "Options: --dry-run  --yes (skip confirmation)  --notes FILE (release notes; default: auto-generated)"
}

version=""; dry_run=false; assume_yes=false; notes_file=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) dry_run=true; shift ;;
        --yes|-y)  assume_yes=true; shift ;;
        --notes)   notes_file="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "Unknown option: $1"; exit 1 ;;
        *)  version="${1#v}"; shift ;;
    esac
done

fail() { echo "✖ $*" >&2; exit 1; }
pass() { echo "✔ $*"; }

[[ -n "$version" ]] || { usage; exit 1; }
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || fail "'$version' is not a valid version (e.g. 1.0.0 or 1.0.0-beta.1)"
prerelease=false
[[ "$version" == *-* ]] && prerelease=true

# Reuse the updater's comparison so release ordering matches what users see.
# (eval rather than `source <(...)`, which doesn't work in macOS's bash 3.2)
eval "$(sed -n '/^version_gt()/,/^}/p' "$REPO_ROOT/files/current/update.sh")"
declare -F version_gt >/dev/null || { echo "Could not load version_gt from update.sh" >&2; exit 1; }

# --- checks -----------------------------------------------------------------
command -v git >/dev/null || fail "git is required"
command -v gh  >/dev/null || fail "GitHub CLI (gh) is required: brew install gh"
gh auth status >/dev/null 2>&1 || fail "Not logged in to GitHub CLI - run: gh auth login"

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$prerelease" == false && "$branch" != main ]]; then
    fail "Stable releases must be made from main (you're on $branch). Use a pre-release version like $version-beta.1 to test from a branch."
fi
pass "Branch: $branch"

git diff --quiet && git diff --cached --quiet || fail "You have uncommitted changes - commit or stash them first"
pass "Working tree is clean"

git fetch --quiet --tags origin
if [[ "$branch" == main ]] && [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
    git merge-base --is-ancestor origin/main HEAD || fail "main is behind origin/main - pull first"
fi
git rev-parse -q --verify "refs/tags/$version" >/dev/null && fail "Tag $version already exists"
pass "Tag $version is free"

current="$(tr -d '[:space:]' < VERSION 2>/dev/null || echo 0.0.0)"
latest_tag=""
while read -r t; do
    if [[ -z "$latest_tag" ]] || version_gt "$t" "$latest_tag"; then latest_tag="$t"; fi
done < <(git tag --list | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+' || true)
for prev in "$current" "${latest_tag:-0.0.0}"; do
    version_gt "$version" "$prev" || [[ "$version" == "$current" && "$prev" == "$current" ]] \
        || fail "$version must be newer than $prev"
done
pass "Version $version is newer than ${latest_tag:-any existing release}"

bash -n install.sh                       || fail "install.sh has a syntax error"
bash -n files/current/update.sh          || fail "update.sh has a syntax error"
zsh  -n files/current/poketerm           || fail "poketerm has a syntax error"
for f in files/current/*.py; do
    python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$f" || fail "$f has a syntax error"
done
pass "Syntax checks passed"

# Full end-to-end test suite in a throwaway HOME (see test.sh).
echo "Running tests..."
if test_output="$(bash "$REPO_ROOT/test.sh" 2>&1)"; then
    pass "Test suite: $(printf '%s\n' "$test_output" | tail -n 1)"
else
    printf '%s\n' "$test_output" | grep -E "✖|^      " >&2
    fail "Test suite failed - fix the failures above (./test.sh -v shows details)"
fi

[[ -z "$notes_file" || -f "$notes_file" ]] || fail "Notes file $notes_file not found"

if [[ "$dry_run" == true ]]; then
    echo ""
    echo "Dry run OK - $version is ready to release. Nothing was changed."
    exit 0
fi

# --- release ------------------------------------------------------------------
echo ""
if [[ "$prerelease" == true ]]; then
    echo "About to publish PRE-RELEASE $version from $branch (only 'poketerm --update --beta' users get it)."
else
    echo "About to publish $version from $branch. All users will be offered this update."
fi
if [[ "$assume_yes" != true ]]; then
    read -r -p "Continue? [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]] || { echo "Cancelled."; exit 1; }
fi

if [[ "$version" != "$current" ]]; then
    printf '%s\n' "$version" > VERSION
    git add VERSION
    git commit -q -m "Release $version"
    pass "Committed VERSION $version"
fi

git tag -a "$version" -m "poketerm $version"
git push -q origin "$branch"
git push -q origin "$version"
pass "Pushed $branch and tag $version"

gh_args=(release create "$version" --title "$version")
[[ "$prerelease" == true ]] && gh_args+=(--prerelease) || gh_args+=(--latest)
if [[ -n "$notes_file" ]]; then gh_args+=(--notes-file "$notes_file"); else gh_args+=(--generate-notes); fi
gh "${gh_args[@]}"
pass "Published GitHub release $version"

echo ""
echo "Done. Users will see the update notice within a day, or can run: poketerm --update$([[ "$prerelease" == true ]] && echo " --beta")"
