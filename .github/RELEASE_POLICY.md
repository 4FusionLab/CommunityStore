# Community Store automatic release policy

[Русская версия](RELEASE_POLICY.ru.md)

- On a push or PR merge to `main`, CI compares the shipped paths in `.github/release-paths.txt` with the most recent published ZIP release whose tag is an ancestor of the current commit. Before the first ZIP release, it checks the current push range. PR and `workflow_dispatch` runs only validate the project.
- GitVersion.Tool 6.8.2 derives `MAJOR.MINOR.PATCH` from full Git history. Use Conventional Commits. CI builds the versioned ZIP and SHA-256, stamps its embedded manifest and verifies it with shared 4FusionLab Actions.
- All Linux/macOS/Windows offline tests must succeed. This does **not** prove native Fusion acceptance.
- Only the release job can write contents. It creates a previously nonexistent immutable `vMAJOR.MINOR.PATCH` tag and GitHub Release automatically. No manually pushed tags; never overwrite tags, releases, or artifacts.
- Documentation, test, workflow, policy and GitVersion changes alone do not publish a release. After the first ZIP release, any push to `main` can retry publication of earlier unreleased changes to shipped files.
- Shared Actions are pinned to audited commit `2e961620a068c3ad754f6e1be05fb6e4ac68e843` in 4FusionLab/.github. See [shared release platform](https://github.com/4FusionLab/.github/blob/main/RELEASE_PLATFORM.md).

