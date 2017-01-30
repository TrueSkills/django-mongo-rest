class ApiException(Exception):
    '''Caught by ApiExceptionMiddleware. Converts error into json response'''
    def __init__(self, message, status_code, error_code='', **extra_data):
        super(ApiException, self).__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.__dict__.update(extra_data)
