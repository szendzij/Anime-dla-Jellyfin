"""Wspólny interfejs źródeł: search -> episodes -> links.

Dwa źródła pod jednym API, żeby redirector, panel i cron nie wiedziały,
skąd właściwie pochodzi odcinek:

    oa  — ogladajanime.pl (wymaga logowania, prefiks odcinka pusty: /e/226672)
    cda — cda.pl (bez logowania, prefiks: /e/cda-23576492f)

Prefiks jest częścią trwałego adresu w `.strm`. Dla ogladajanime zostaje pusty,
bo biblioteka użytkownika już zawiera pliki z gołym numerem odcinka — zmiana
unieważniłaby całą kolekcję.
"""

from __future__ import annotations

from pathlib import Path


class ProviderError(RuntimeError):
    """Bazowy wyjątek dla wszystkich błędów providerów treści."""
    pass


from oa import Episode, Link, OgladajAnime, pick_best
from providers.cda import Cda, CdaError, EP_PREFIX as CDA_PREFIX
from providers.shinden import Shinden, ShindenError, EP_PREFIX as SHINDEN_PREFIX

__all__ = [
    "Episode",
    "Link",
    "Source",
    "ProviderError",
    "CdaError",
    "ShindenError",
    "build_sources",
    "split_ref",
    "pick_best",
]

CDA = "cda"
OA = "oa"
SHINDEN = "shinden"


def split_ref(ref: str) -> tuple[str, str]:
    """Rozkłada identyfikator odcinka z adresu `.strm` na (źródło, ep_id)."""
    if ref.startswith(SHINDEN_PREFIX):
        return SHINDEN, ref[len(SHINDEN_PREFIX) :]
    if ref.startswith(CDA_PREFIX):
        return CDA, ref[len(CDA_PREFIX) :]
    return OA, ref


class Source:
    """Jedno źródło treści w interfejsie, którego używa reszta programu."""

    def __init__(self, name: str, label: str, api, prefix: str = "", needs_login: bool = False) -> None:
        self.name = name
        self.label = label
        self.api = api
        self.prefix = prefix
        self.needs_login = needs_login

    # --- przygotowanie --------------------------------------------------------

    def ensure_ready(self) -> None:
        if self.needs_login:
            self.api.ensure_login()

    # --- interfejs ------------------------------------------------------------

    def search(self, query: str) -> list[dict]:
        """Wyniki wyszukiwania: {ref, title, poster, kind}."""
        raise NotImplementedError

    def episodes(self, ref: str) -> list[Episode]:
        raise NotImplementedError

    def links(self, ep_id: str) -> list[Link]:
        raise NotImplementedError

    def strm_url(self, base_url: str, ep_id: str) -> str:
        """Adres do wpisania w `.strm` — zawsze przez lokalny redirector."""
        return f"{base_url.rstrip('/')}/e/{self.prefix}{ep_id}"


class OaSource(Source):
    def __init__(self, api: OgladajAnime) -> None:
        super().__init__(OA, "ogladajanime", api, prefix="", needs_login=True)

    def search(self, query: str) -> list[dict]:
        return [
            {
                "ref": i["slug"],
                "title": i["title"],
                "poster": i.get("poster") or "",
                "kind": "series",
            }
            for i in self.api.search_results(query)
        ]

    def episodes(self, ref: str) -> list[Episode]:
        return self.api.episodes(ref)

    def links(self, ep_id: str) -> list[Link]:
        return self.api.links(ep_id)


class CdaSource(Source):
    def __init__(self, api: Cda) -> None:
        super().__init__(CDA, "CDA", api, prefix=CDA_PREFIX, needs_login=False)

    def search(self, query: str) -> list[dict]:
        return self.api.search(query)

    def episodes(self, ref: str) -> list[Episode]:
        return self.api.episodes(ref)

    def links(self, ep_id: str) -> list[Link]:
        return self.api.links(ep_id)


class ShindenSource(Source):
    """Shinden jako katalog: odcinki + rozwiązywanie playerów (na razie CDA)."""

    def __init__(self, api: Shinden) -> None:
        super().__init__(SHINDEN, "Shinden", api, prefix=SHINDEN_PREFIX, needs_login=False)

    def search(self, query: str) -> list[dict]:
        return self.api.search(query)

    def episodes(self, ref: str) -> list[Episode]:
        return self.api.episodes(ref)

    def links(self, ep_id: str) -> list[Link]:
        return self.api.links(ep_id)

    def describe(self, ref: str) -> str:
        return self.api.describe(ref)


def build_sources(
    session_file: Path | str | None = None,
    quality: int | None = None,
) -> dict[str, Source]:
    """Tworzy oba źródła. Konfiguracja poświadczeń ogladajanime idzie jak dotąd:
    zmienne środowiskowe OA_LOGIN / OA_PASS albo zapisana sesja."""
    kw = {"session_file": Path(session_file).expanduser()} if session_file else {}
    return {
        OA: OaSource(OgladajAnime(**kw)),
        CDA: CdaSource(Cda(quality=quality)),
        SHINDEN: ShindenSource(Shinden(quality=quality)),
    }
