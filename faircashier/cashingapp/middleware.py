class DisableSameSiteMiddleware:
    """
    Middleware to disable SameSite cookie attribute in development.
    WARNING: Only use in development!
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        
        # Patch cookies to remove SameSite
        if hasattr(response, 'cookies'):
            for cookie in response.cookies.values():
                cookie['samesite'] = ''
        
        return response