"""Rate limiter slowapi partagé.

Isolé dans son propre module pour que `app.py` ET les routers importent la même
instance sans import circulaire (app.py importe les routers, qui importent ce
limiter — jamais l'inverse). app.py l'attache ensuite à `app.state.limiter`.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

# Limite par défaut appliquée à toutes les routes ; chaque endpoint peut la
# surcharger via @limiter.limit(...). Clé = IP du client (protège la clé API).
limiter = Limiter(key_func=get_remote_address, default_limits=["200/day"])
