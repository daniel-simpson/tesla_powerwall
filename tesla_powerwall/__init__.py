import logging
import sys

import requests
from urllib.parse import urljoin, urlparse, urlunparse, urlsplit, urlunsplit
from requests import Session
from requests.packages.urllib3.exceptions import InsecureRequestWarning

from .responses import MetersResponse, SiteinfoResponse, SitemasterResponse, CustomerRegistrationResponse

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

VERSION = "0.1.4"

STATUS_UP = "StatusUp"

GRID_STATUS_SYSTEM_GRID_UP = "SystemGridConnected"
GRID_STATUS_SYSTEM_GRID_DOWN = "SystemIslandedActive"
GRID_STATUS_SYSTEM_GRID_RESTORED_NO_SYNC = "SystemTransitionToGrid"

OPERATION_MODE_SELF_CONSUMPTION = "self_consumption"
OPERATION_MODE_BACKUP = "backup"
OPERATION_MODE_TIME_OF_USE = "autonomus"
OPERATION_MODE_SCHEDULER = "scheduler"

BACKUP_RESERVE_PERCENTAGE_5 = 10
BACKUP_RESERVE_PERCENTAGE_16 = 20
BACKUP_RESERVE_PERCENTAGE_20 = 24
BACKUP_RESERVE_PERCENTAGE_21 = 24.6
BACKUP_RESERVE_PERCENTAGE_30 = 33
BACKUP_RESERVE_PERCENTAGE_100 = 100


class ApiError(Exception):
    def __init__(self, error):
        super().__init__(f"Power Wall api error: {error}")


class PowerWallUnreachableError(Exception):
    def __init__(self):
        super().__init__(f"Site master or Power wall is unreachable!")


class AccessDeniedError(Exception):
    def __init__(self, resource, error=None):
        msg = f"Access denied for resource {resource}"
        if msg is not None:
            msg = f"{msg}: {error}"
        super().__init__(msg)


class PowerWall:
    def __init__(self, endpoint, timeout=10, http_session=None, verify_ssl=False):
        if endpoint.startswith("https"):
            self._endpoint = endpoint
        elif endpoint.startswith("http"):
            self._endpoint = endpoint.replace("http", "https")
        else:
            self._endpoint = f"https://{endpoint}"

        if not self._endpoint.endswith("api") and not self._endpoint.endswith("/"):
            self._endpoint += "/api/"
        elif self._endpoint.endswith("api"):
            self._endpoint += "/"
        elif self._endpoint.endswith("/"):
            self._endpoint += "api/"

        self._timeout = timeout
        self._http_session = http_session if http_session else Session()
        self._http_session.verify = verify_ssl
        self._password = None
        self._username = None

    def _process_response(self, response):
        if response.status_code == 401 or response.status_code == 403:
            response_json = None
            try:
                response_json = response.json()
            except Exception:
                raise AccessDeniedError(response.request.path_url)
            else:
                raise AccessDeniedError(
                    response.request.path_url, response_json["error"])

        if response.status_code == 502:
            raise PowerWallUnreachableError()

        response_json = response.json()

        if "error" in response_json:
            raise ApiError(response_json["error"])

        return response_json

    def _get(self, path: str, needs_authentication=False, headers: dict = {}):
        if needs_authentication is True and not "Authorization" in self._http_session.headers.keys():
            raise ApiError(f"Authentication required to access {path}")

        response = self._http_session.get(
            url=urljoin(self._endpoint, path),
            timeout=self._timeout,
            headers=headers,
        )

        return self._process_response(response)

    def _post(self, path: str, payload: dict, needs_authentication=False, headers: dict = {}):
        if needs_authentication and not "Authorization" in self._http_session.headers.keys():
            raise ApiError(f"Authentication required to access {path}")

        response = self._http_session.post(
            url=urljoin(self._endpoint, path),
            data=payload,
            timeout=self._timeout,
            headers=headers,
        )

        return self._process_response(response)

    def login(self, username: str, email: str, password: str):
        if username not in ("installer", "custumer"):
            raise ValueError(
                f"Username must be 'installer' or 'custumer' not {username}")

        response = self._post(
            "api/login/Basic",
            {"username": username, "email": email,
                "password": password, "force_sm_off": True},
        )

        token = response["token"]

        self._http_session.headers["Authorization"] = "Bearer " + token

    def run(self):
        self._get("sitemaster/run", True)

    def stop(self):
        self._get("sitemaster/stop", True)

    def set_run_for_commissioning(self):
        self._post("sitemaster/run_for_commissioning", True)

    @property
    def charge(self):
        return self._get("system_status/soe")["percentage"]

    @property
    def sitemaster(self):
        return SitemasterResponse(self._get("sitemaster"))

    @property
    def meters(self):
        return self._get("meters/aggregates")

    @property
    def solar(self):
        return MetersResponse(self.meters["solar"])

    @property
    def grid(self):
        return MetersResponse(self.meters["site"])

    @property
    def load(self):
        return MetersResponse(self.meters["load"])

    @property
    def battery(self):
        return MetersResponse(self.meters["battery"])

    @property
    def busway(self):
        return MetersResponse(self.meters["busway"])

    @property
    def frequency(self):
        return MetersResponse(self.meters["frequency"])

    @property
    def generator(self):
        return MetersResponse(self.meters["generator"])

    @property
    def solar_detailed(self):
        return self._get("meters/solar")

    @property
    def grid_status(self):
        return self._get("system_status/grid_status")["grid_status"]

    @property
    def grid_services_active(self):
        return self._get("system_status/grid_status")["grid_services_active"]

    @property
    def site_info(self):
        return self._get("site_info")

    @property
    def site_info_status(self):
        return self._get("site_info/status")

    def set_site_name(self, site_name: str):
        return self._post("site_info/site_name", {"site_name": site_name}, True)

    @property
    def status(self):
        return self._get("status")

    @property
    def device_type(self):
        return self._get("device_type")

    @property
    def customer_registration(self):
        return CustomerRegistrationResponse(self._get("customer/registration"))

    @property
    def home_power(self):
        return self.load.instant_power

    @property
    def grid_power(self):
        return self.grid.instant_power

    @property
    def solar_power(self):
        return self.solar.instant_power

    @property
    def battery_power(self):
        return self.battery.instant_power

    @property
    def operation(self):
        return self._get("operation", True)

    @property
    def mode(self):
        return self.operation["mode"]

    @property
    def backup_preserve_percentage(self):
        return self.operation["backup_reserve_percentage"]

    def set_mode_and_backup_preserve_percentage(self, mode, percentage):
        self._post("operation", {"mode": mode, "percentage": percentage})

    def set_backup_preserve_percentage(self, percentage):
        self.set_mode_and_backup_preserve_percentage(self.mode, percentage)

    def set_mode(self, mode):
        self.set_mode_and_backup_preserve_percentage(
            mode, self.backup_preserve_percentage)

    def is_sending_to_grid(self):
        return self.grid_power < 0

    def is_drawing_from_grid(self):
        return not self.is_sending_to_grid()

    def is_sending_to_battery(self):
        return self.battery_power < 0

    def is_drawing_from_battery(self):
        return not self.is_sending_to_battery()

    def is_sending_to_solar(self):
        return self.solar_power < 0

    def is_drawing_from_solar(self):
        return not self.is_sending_to_solar()

    def __del__(self):
        if self._http_session is not None:
            self._http_session.close()
