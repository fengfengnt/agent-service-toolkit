from typing import Literal

from starlette.requests import Request
from starlette.responses import Response

from xiaohe.contracts import ServiceError

HEADER = "X-Xiaohe-View-Version"


def parse_view_version(request: Request, *, v2_only: bool = False) -> Literal[1, 2]:
    values = request.headers.getlist(HEADER)
    if not values and not v2_only:
        return 1
    if values == ["2"]:
        return 2
    if values == ["1"] and not v2_only:
        return 1
    raise ServiceError("VIEW_VERSION_UNSUPPORTED", 406)


def set_view_version(response: Response, version: Literal[1, 2]) -> None:
    response.headers[HEADER] = str(version)
