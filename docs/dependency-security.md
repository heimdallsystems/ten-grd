# Certificate and packaging-tool security

CI pins pip to 26.2.2 using the SHA-256-verified wheel recommended by the
Python security announcement. Bootstrap runs before dependency downloads.
Subsequent installs inherit a pip constraint and certifi>=2026.10.19 floor;
publisher installs also require sigstore>=4.5.1. Package metadata requires the
certifi floor, so incompatible older pins fail dependency resolution.

https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576

Publishing uses Twine 7 and pypi-attestations in the patched runner interpreter,
retaining OIDC Trusted Publishing and publish attestations. The former Docker
publisher has its own pip, unaffected by upgrading the runner's pip. Keep the
existing publish.yml filename and pypi environment for PyPI's trusted identity.

Copy the inline bootstrap along with the Invariant GitHub/GitLab CI templates.
After creating a new virtual environment, bootstrap its pip too; installing
certifi does not update pip's vendored certificate bundle. Existing environments
need their own updates. If uv is used locally, upgrade it to at least 0.13.4.
Rebuild container images with updated operating-system certificate stores.

No package version is bumped by this change and existing PyPI artifacts are
immutable. New package metadata takes effect for source installs and the next
release. Runtime policy/isolation semantics are unchanged.
