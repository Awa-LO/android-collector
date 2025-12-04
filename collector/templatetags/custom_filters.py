# collector/templatetags/custom_filters.py
# Créez ce fichier dans le dossier: collector/templatetags/

from django import template

register = template.Library()

@register.filter
def endswith(value, arg):
    """Vérifie si une chaîne se termine par un suffixe donné"""
    if value and arg:
        return str(value).endswith(str(arg))
    return False