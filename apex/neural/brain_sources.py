"""Catalogue des sources du corpus du Cerveau APEX v2 (textes réels, licences libres, provenance
vérifiable).

`local_sources` : ce qui est sur le poste (bibliothèque standard Python, documentation et code d'APEX).
`ubuntu_sources` : documentation technique anglaise et française de paquets Ubuntu SIGNÉS (manuels, guides,
pages de manuel, traductions humaines des logiciels). `pypi_sources` : archives sources de bibliothèques
Python à licence permissive (code et documentation).

Les plafonds (`max_bytes`) équilibrent le mélange ; le poids de chaque groupe à l'entraînement est fixé à la
tokenisation (`apex.neural.shards`)."""

from __future__ import annotations

import sys
import sysconfig
from pathlib import Path

from apex.neural.corpus import SourceSpec

MB = 1_000_000
_HTML_EXCLUDE = (
    "*genindex*",
    "*py-modindex*",
    "*/search.html",
    "*/_static/*",
    "*/_sources/*",
    "*/_images/*",
    "*/objects.inv",
    "*/searchindex*",
    "*/_downloads/*",
)


def local_sources(repo: Path) -> list[SourceSpec]:
    stdlib = Path(sysconfig.get_paths()["stdlib"])
    return [
        SourceSpec(
            "python-stdlib",
            "local",
            str(stdlib),
            include=("*.py",),
            exclude=(
                "test/*",
                "*/test/*",
                "*/tests/*",
                "idlelib/*",
                "lib2to3/*",
                "site-packages/*",
                "dist-packages/*",
                "turtledemo/*",
                "*/_pydecimal.py",
                "encodings/*",
            ),
            lang="code",
            doc_kind="code",
            license=f"PSF-2.0 (Python {sys.version_info.major}.{sys.version_info.minor})",
            max_bytes=40 * MB,
        ),
        SourceSpec(
            "apex-docs",
            "local",
            str(repo / "docs"),
            include=("*.md",),
            lang="fr",
            license="APEX (projet de l'utilisateur)",
        ),
        SourceSpec(
            "apex-code",
            "local",
            str(repo / "apex"),
            include=("*.py",),
            lang="code",
            doc_kind="code",
            license="APEX (projet de l'utilisateur)",
            max_bytes=25 * MB,
        ),
    ]


def _ubuntu(
    name: str,
    package: str,
    *,
    lang: str = "en",
    include: tuple[str, ...] = ("*",),
    exclude: tuple[str, ...] = (),
    max_mb: int = 20,
) -> SourceSpec:
    return SourceSpec(
        name,
        "ubuntu",
        package,
        include=include,
        exclude=_HTML_EXCLUDE + exclude,
        lang=lang,
        max_bytes=max_mb * MB,
    )


_DOCS = ("usr/share/doc/*.html", "usr/share/doc/*.txt", "usr/share/info/*", "usr/share/man/*")


def ubuntu_sources() -> list[SourceSpec]:
    english = [
        _ubuntu(
            "python-docs", "python3.12-doc", include=("usr/share/doc/python3.12/html/*.html",), max_mb=30
        ),
        _ubuntu("django-docs", "python-django-doc", include=_DOCS, max_mb=25),
        _ubuntu("flask-docs", "python-flask-doc", include=_DOCS),
        _ubuntu("requests-docs", "python-requests-doc", include=_DOCS),
        _ubuntu("pytest-docs", "python-pytest-doc", include=_DOCS),
        _ubuntu("sphinx-docs", "sphinx-doc", include=_DOCS),
        _ubuntu("sqlalchemy-docs", "python-sqlalchemy-doc", include=_DOCS, max_mb=15),
        _ubuntu("attrs-docs", "python-attr-doc", include=_DOCS),
        _ubuntu(
            "git-docs",
            "git-doc",
            include=("usr/share/doc/git-doc/*.txt", "usr/share/doc/git-doc/*.html"),
            exclude=("*RelNotes*",),
        ),
        _ubuntu("make-docs", "make-doc", include=_DOCS),
        _ubuntu("bash-docs", "bash-doc", include=_DOCS),
        _ubuntu("gawk-docs", "gawk-doc", include=_DOCS),
        _ubuntu("gnu-standards", "gnu-standards", include=_DOCS),
        _ubuntu("debian-reference-en", "debian-reference-en", include=_DOCS),
        _ubuntu("debian-developers-reference", "developers-reference", include=_DOCS),
        _ubuntu("debian-maint-guide", "maint-guide", include=_DOCS),
        _ubuntu("postgresql-docs", "postgresql-doc-16", include=_DOCS, max_mb=15),
        _ubuntu("sqlite-docs", "sqlite3-doc", include=_DOCS, max_mb=10),
        _ubuntu("nodejs-docs", "nodejs-doc", include=_DOCS, max_mb=10),
        _ubuntu("gdb-docs", "gdb-doc", include=_DOCS),
        _ubuntu("gcc-docs", "gcc-13-doc", include=_DOCS),
        _ubuntu("cmake-docs", "cmake-doc", include=_DOCS, max_mb=10),
        _ubuntu("linux-manpages", "manpages", include=("usr/share/man/*",)),
        _ubuntu("linux-manpages-dev", "manpages-dev", include=("usr/share/man/*",)),
        _ubuntu(
            "rust-docs",
            "rust-doc",
            include=tuple(
                f"usr/share/doc/rust-doc/html/{book}/*.html"
                for book in (
                    "book",
                    "reference",
                    "rust-by-example",
                    "nomicon",
                    "cargo",
                    "edition-guide",
                    "rustc",
                )
            )
            + tuple(
                f"usr/share/doc/rust/html/{book}/*.html"
                for book in (
                    "book",
                    "reference",
                    "rust-by-example",
                    "nomicon",
                    "cargo",
                    "edition-guide",
                    "rustc",
                )
            ),
            max_mb=30,
        ),
        _ubuntu("vim-docs", "vim-doc", include=_DOCS, max_mb=10),
        _ubuntu("zsh-docs", "zsh-doc", include=_DOCS),
        _ubuntu("emacs-manuals", "emacs-common", include=("usr/share/info/*",), max_mb=15),
    ]
    french = [
        _ubuntu("manpages-fr", "manpages-fr", lang="fr", include=("usr/share/man/*",)),
        _ubuntu("manpages-fr-dev", "manpages-fr-dev", lang="fr", include=("usr/share/man/*",)),
        _ubuntu("debian-reference-fr", "debian-reference-fr", lang="fr", include=_DOCS),
        _ubuntu("debian-developers-reference-fr", "developers-reference-fr", lang="fr", include=_DOCS),
        _ubuntu("debian-maint-guide-fr", "maint-guide-fr", lang="fr", include=_DOCS),
        _ubuntu("aptitude-docs-fr", "aptitude-doc-fr", lang="fr", include=_DOCS),
        _ubuntu("gnome-help-fr", "gnome-user-docs-fr", lang="fr", include=("usr/share/help/fr/*",)),
        _ubuntu("kicad-docs-fr", "kicad-doc-fr", lang="fr", include=_DOCS),
        _ubuntu("lilypond-docs-fr", "lilypond-doc-html-fr", lang="fr", include=_DOCS, max_mb=25),
        _ubuntu("ubuntu-packaging-fr", "ubuntu-packaging-guide-html-fr", lang="fr", include=_DOCS),
        _ubuntu("expeyes-docs-fr", "expeyes-doc-fr", lang="fr", include=_DOCS),
    ]
    translations = [
        SourceSpec("traductions-fr", "ubuntu_mo", "language-pack-fr-base", lang="fr", max_bytes=25 * MB),
        SourceSpec(
            "traductions-gnome-fr", "ubuntu_mo", "language-pack-gnome-fr-base", lang="fr", max_bytes=15 * MB
        ),
    ]
    return english + french + translations


#: Bibliothèques Python à licence permissive (vérifiée sur PyPI à chaque construction ; refusée sinon).
PYPI_PACKAGES = (
    "requests",
    "flask",
    "werkzeug",
    "jinja2",
    "click",
    "attrs",
    "pydantic",
    "fastapi",
    "starlette",
    "httpx",
    "rich",
    "typer",
    "sqlalchemy",
    "alembic",
    "pytest",
    "hypothesis",
    "black",
    "isort",
    "mypy",
    "django",
    "networkx",
    "sympy",
    "more-itertools",
    "toolz",
    "boltons",
    "arrow",
    "python-dateutil",
    "packaging",
    "setuptools",
    "pip",
    "tox",
    "coverage",
    "sphinx",
    "pygments",
    "markdown",
    "beautifulsoup4",
    "pyyaml",
    "tomli",
    "msgpack",
    "invoke",
    "celery",
    "kombu",
    "redis",
    "aiohttp",
    "trio",
    "anyio",
    "uvicorn",
    "gunicorn",
    "marshmallow",
    "structlog",
    "loguru",
    "tenacity",
    "cachetools",
    "sortedcontainers",
    "scikit-learn",
    "pandas",
    "numpy",
    "scipy",
    "matplotlib",
    "pillow",
    "tqdm",
    "docutils",
    "jsonschema",
    "pluggy",
    "wheel",
    "virtualenv",
    "pyflakes",
    "pycodestyle",
    "flake8",
    "pylint",
    "astroid",
    "parso",
    "jedi",
    "prompt-toolkit",
    "ipython",
    "nbformat",
    "bottle",
    "falcon",
    "tornado",
    "twisted",
    "scrapy",
    "pexpect",
    "psutil",
    "watchdog",
    "humanize",
    "pendulum",
    "babel",
    "dataset",
    "peewee",
    "pony",
)


def pypi_sources(packages: tuple[str, ...] = PYPI_PACKAGES) -> list[SourceSpec]:
    return [
        SourceSpec(
            f"pypi-{name}",
            "pypi",
            name,
            include=("*.py", "*.rst", "*.md", "*.txt"),
            exclude=(
                "*/tests/*",
                "*/test/*",
                "*/testing/*",
                "*LICENSE*",
                "*/vendor/*",
                "*/_vendor/*",
                "*/locale/*",
                "*CHANGES*",
                "*CHANGELOG*",
                "*HISTORY*",
                "*/changelog/*",
                "*/releases/*",
                "*/release-notes/*",
                "*/whatsnew/*",
                "*requirements*.txt",
                "*/data/*",
                "*AUTHORS*",
                "*/benchmarks/*",
            ),
            lang="code",
            doc_kind="code",
            max_bytes=8 * MB,
        )
        for name in packages
    ]


def validation_sources(repo: Path) -> list[SourceSpec]:
    """Toutes les sources accessibles depuis l'environnement de développement (le corpus du modèle de
    validation). Le corpus du grand entraînement (GPU du poste) y ajoute des jeux de textes publics."""
    return local_sources(repo) + ubuntu_sources() + pypi_sources()


#: Jeux de textes publics du Hub Hugging Face pour le grand entraînement (PC de l'utilisateur ; ce Hub n'est
#: pas joignable depuis l'environnement de développement : chemins à confirmer au premier téléchargement,
#: une source en échec est rapportée sans arrêter les autres). Textes du web écrits par des humains,
#: licences ouvertes.
HF_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        "fineweb-edu",
        "hf_parquet",
        "HuggingFaceFW/fineweb-edu@main:sample/10BT",
        lang="en",
        license="ODC-By 1.0 (FineWeb-Edu, Hugging Face)",
        max_bytes=5_000 * MB,
    ),
    SourceSpec(
        "fineweb2-fr",
        "hf_parquet",
        "HuggingFaceFW/fineweb-2@main:data/fra_Latn/train",
        lang="fr",
        license="ODC-By 1.0 (FineWeb-2, Hugging Face)",
        max_bytes=3_000 * MB,
    ),
    SourceSpec(
        "wikipedia-fr",
        "hf_parquet",
        "wikimedia/wikipedia@main:20231101.fr",
        lang="fr",
        license="CC BY-SA 4.0 (Wikipédia)",
        max_bytes=1_500 * MB,
    ),
    SourceSpec(
        "wikipedia-en",
        "hf_parquet",
        "wikimedia/wikipedia@main:20231101.en",
        lang="en",
        license="CC BY-SA 4.0 (Wikipedia)",
        max_bytes=1_000 * MB,
    ),
)


#: Bibliothèques supplémentaires pour le grand entraînement (plus de code : moins de répétitions). La
#: licence de chacune est vérifiée sur PyPI à la construction ; une licence non permissive est refusée.
PYPI_PACKAGES_GPU = (
    "certifi",
    "charset-normalizer",
    "idna",
    "urllib3",
    "six",
    "wrapt",
    "decorator",
    "simplejson",
    "ujson",
    "regex",
    "chardet",
    "html5lib",
    "lxml",
    "cssselect",
    "parsel",
    "w3lib",
    "itsdangerous",
    "markupsafe",
    "mako",
    "cerberus",
    "voluptuous",
    "schema",
    "jsonpointer",
    "jsonpatch",
    "python-slugify",
    "inflection",
    "inflect",
    "tabulate",
    "prettytable",
    "texttable",
    "colorama",
    "termcolor",
    "blessed",
    "urwid",
    "docopt",
    "fire",
    "plumbum",
    "sh",
    "tomlkit",
    "toml",
    "configobj",
    "python-dotenv",
    "dynaconf",
    "environs",
    "appdirs",
    "platformdirs",
    "filelock",
    "portalocker",
    "pathspec",
    "send2trash",
    "schedule",
    "apscheduler",
    "croniter",
    "dateparser",
    "pytz",
    "tzlocal",
    "iso8601",
    "python-json-logger",
    "colorlog",
    "coloredlogs",
    "backoff",
    "retrying",
    "cachelib",
    "diskcache",
    "dogpile.cache",
    "intervaltree",
    "bitarray",
    "bitstring",
    "construct",
    "cffi",
    "pycparser",
    "jmespath",
    "pyrsistent",
    "immutables",
    "frozendict",
    "multidict",
    "yarl",
    "aiosignal",
    "frozenlist",
    "async-timeout",
    "aiofiles",
    "asgiref",
    "h11",
    "httpcore",
    "sniffio",
    "websockets",
    "wsproto",
    "hpack",
    "hyperframe",
    "h2",
    "requests-toolbelt",
    "responses",
    "freezegun",
    "factory-boy",
    "faker",
    "mock",
    "pytest-mock",
    "pytest-cov",
    "pytest-xdist",
    "execnet",
    "iniconfig",
    "nose2",
    "testfixtures",
    "parameterized",
    "ddt",
    "pyfakefs",
    "vcrpy",
    "sqlparse",
    "pypika",
    "tinydb",
    "persistent",
    "transaction",
    "graphviz",
    "pydot",
    "mpmath",
    "uncertainties",
    "pint",
    "quantities",
    "astropy",
    "scikit-image",
    "imageio",
    "pywavelets",
    "numexpr",
    "bottleneck",
    "patsy",
    "xarray",
    "dask",
    "cloudpickle",
    "partd",
    "fsspec",
    "joblib",
    "threadpoolctl",
    "pyparsing",
    "ply",
    "lark",
    "parsimonious",
    "textx",
    "arpeggio",
    "ruamel.yaml",
    "cookiecutter",
    "copier",
    "doit",
    "scons",
    "meson",
    "build",
    "flit",
    "hatchling",
    "poetry-core",
    "pdm-backend",
    "setuptools-scm",
    "versioneer",
    "bump2version",
    "towncrier",
    "twine",
    "readme-renderer",
    "pkginfo",
    "check-manifest",
    "bandit",
    "radon",
    "mccabe",
    "vulture",
    "autopep8",
    "yapf",
    "docformatter",
)


def gpu_sources(repo: Path) -> list[SourceSpec]:
    """Corpus du grand entraînement : les sources de validation (code et documentation vérifiés), davantage
    de bibliothèques Python, et les jeux de textes publics (anglais et français) du Hub."""
    extra = tuple(name for name in dict.fromkeys(PYPI_PACKAGES_GPU) if name not in PYPI_PACKAGES)
    return validation_sources(repo) + pypi_sources(extra) + list(HF_SOURCES)
