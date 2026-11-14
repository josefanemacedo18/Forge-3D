"""Exceções do motor. As mensagens são exibidas ao usuário (PT-BR)."""


class EngineError(Exception):
    """Erro esperado, com mensagem clara para o usuário."""


class CancelledError(EngineError):
    """Operação cancelada pelo usuário."""

    def __init__(self, message: str = "Operação cancelada pelo usuário."):
        super().__init__(message)
