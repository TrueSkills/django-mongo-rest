ORG_PERMISSION_SESSION_KEY = 'perm_org'

def set_org(request, company_id):
    if company_id:
        request.session[ORG_PERMISSION_SESSION_KEY] = company_id
    else:
        request.session.pop(ORG_PERMISSION_SESSION_KEY, None)

def get_org(request):
    org_id = request.session.get(ORG_PERMISSION_SESSION_KEY)
    if not org_id:
        return None
    return org_id
