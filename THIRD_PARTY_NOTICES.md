# Third-party notices

This file covers the exact dependency graph recorded in `package-lock.json`
and `requirements-build.lock`. These packages are development or build tools;
their source and binaries are not copied into this repository.

The generated reconciliation is in
[`reports/dependency-licenses.json`](reports/dependency-licenses.json). Unknown
packages, versions, or license expressions fail the report generator.

## JavaScript development dependencies

### Playwright 1.63.0 and Playwright Core 1.63.0

License: Apache License 2.0. The complete license is in [`LICENSE`](LICENSE).

Upstream notice:

> Playwright  
> Copyright (c) Microsoft Corporation
>
> This software contains code derived from the Puppeteer project, available
> under the Apache License 2.0.

Browser binaries downloaded separately by Playwright are not distributed in
this repository or in the clean source candidate.

## CI-only actions

The public workflow references `actions/checkout` 7.0.1,
`actions/setup-python` 7.0.0, `actions/setup-node` 7.0.0, and
`actions/upload-artifact` 7.0.1 by immutable commit. These MIT-licensed actions
run on GitHub-hosted infrastructure; their source and runtime bundles are not
copied into this source candidate. Exact commits and review status are recorded
in the private release inventory.

## Python build dependencies

### Hatchling 1.27.0

MIT License

Copyright (c) 2021-present Ofek Lev

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

### Packaging 26.3

License expression: Apache-2.0 OR BSD-2-Clause. This distribution elects the
Apache-2.0 option; the complete Apache License 2.0 is in [`LICENSE`](LICENSE).

Copyright (c) Donald Stufft and individual contributors.

### Pathspec 1.1.1

License: Mozilla Public License 2.0. The complete license is in
[`licenses/MPL-2.0.txt`](licenses/MPL-2.0.txt).

Author metadata: Caleb P. Burns.

### Pluggy 1.6.0

The MIT License (MIT)

Copyright (c) 2015 holger krekel (rather uses bitbucket/hpk42)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

### Trove Classifiers 2026.9.21.13

License: Apache License 2.0. The complete license is in [`LICENSE`](LICENSE).

Author metadata: The PyPI Admins.

## Runtime prerequisites

Python, Node.js, browsers, and operating-system components are prerequisites,
not vendored repository artifacts. Their licenses apply to their respective
installations and are outside this source distribution.
