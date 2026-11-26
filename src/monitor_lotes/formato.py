"""Formato de números para textos en español (Argentina): 1.234,5."""


def num(valor: float, decimales: int = 1) -> str:
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")
