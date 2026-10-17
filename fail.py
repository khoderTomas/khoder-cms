# -*- coding: utf-8 -*-
"""Chyba požadavku: zpráva pro uživatele, strojový kód, HTTP status a podrobnosti.
Bez závislostí, ať ji může použít i hostitel editoru pro vlastní stránky."""


class Fail(Exception):
    def __init__(self, msg, code="error", status=400, details=None):
        super().__init__(msg)
        self.msg, self.code, self.status, self.details = msg, code, status, details or []
