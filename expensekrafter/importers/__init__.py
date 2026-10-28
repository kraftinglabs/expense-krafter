"""Statement parsers. Importing this package registers all of them.

To add a bank: create a module here with a @register-ed StatementParser
subclass and import it below.
"""

from . import sbi_card, tabular  # noqa: F401
from .base import (PARSERS, ParsedStatement, ParsedTxn, PasswordRequired, get_parser,  # noqa: F401
                   parser_options)
