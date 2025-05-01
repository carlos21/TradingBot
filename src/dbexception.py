class DBException(Exception):

    def __init__(self, message="Operation failed"):
        self.message = message
        super().__init__(self.message)

class DBNotFoundException(Exception):

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)