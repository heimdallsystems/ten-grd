# Publishing ten-grd

The PyPI distribution is `ten-grd`; the import is `ten_grd`. This repository's
release workflow publishes through GitHub Actions OIDC, without a stored PyPI API
token.

## One-time PyPI setup

For a new project, sign in at https://pypi.org/manage/account/publishing/ and add a
pending GitHub publisher with these exact values:

- PyPI project: `ten-grd`
- Owner: `heimdallsystems`
- Repository: `ten-grd`
- Workflow filename: `publish.yml`
- Environment: `pypi`

If the project already exists under your account, use its Publishing settings.
Create the matching `pypi` GitHub environment and restrict permitted release tags
as appropriate. PyPI authorization is separate from GitHub repository access.

## Release

1. Review the design docs in docs/ and reconcile source changes. Set matching
   versions in pyproject.toml and src/ten_grd/__init__.py.
2. Commit reviewed changes, including the publishing workflow and reusable tests.
   Push to GitHub and verify tests pass, including the real backend services.
3. Tag the intended commit `vX.Y.Z` and publish its GitHub Release. The workflow
   checks that the tag matches both package versions, reruns core and integration
   tests, builds wheel/sdist, checks metadata, smoke-tests the installed wheel,
   and uploads through the configured trusted publisher.
4. If authentication setup interrupted publication, finish setup then dispatch
   publish.yml on the same version tag. Inspect PyPI first: versions cannot be
   overwritten; do not blindly retry an upload that may have partially succeeded.
5. Verify the PyPI files, hashes and installation from PyPI. Record the release
   commit, Actions run, artifacts, validation and remaining limits in the release notes.

Local preparation: `uv build`, then `uvx twine check --strict dist/*`.
The workflow's tests are release gates. A successful build alone is not validation
of tenant isolation or live backend compatibility.
