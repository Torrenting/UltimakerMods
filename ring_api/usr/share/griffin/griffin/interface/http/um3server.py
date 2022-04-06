from typing import cast

import flask
from urllib.parse import urlparse
from flipflop import WSGIServer

from griffin import dbusif
from griffin.interface.http.authentication.authenticationAPI import AuthenticationAPI
from griffin.interface.http.authentication.authenticationService import AuthenticationService
from griffin.interface.http.endpoints.beep import Beep
from griffin.interface.http.endpoints.blink import Blink
from griffin.interface.http.endpoints.diagnostics import Diagnostics
from griffin.interface.http.endpoints.firmware import Firmware
from griffin.interface.http.endpoints.materials import Materials
from griffin.interface.http.endpoints.messageScreen import MessageScreen
from griffin.interface.http.endpoints.preHeatBed import PreHeatBed
from griffin.interface.http.endpoints.printJob import PrintJob
from griffin.interface.http.endpoints.printJobGcode import PrintJobGcode
from griffin.interface.http.endpoints.printJobContainer import PrintJobContainer
from griffin.interface.http.endpoints.printJobState import PrintJobState
from griffin.interface.http.endpoints.printJobHistory import PrintJobHistory
from griffin.interface.http.endpoints.eventHistory import EventHistory
from griffin.interface.http.endpoints.wifi import Wifi
from griffin.interface.http.endpoints.camera import Camera
from griffin.interface.http.endpoints.validateGCodeHeader import ValidateGCodeHeader
from griffin.interface.http.exposedItems.httpExposedBool import HttpExposedBool
from griffin.interface.http.exposedItems.httpExposedDatetime import HttpExposedDatetime
from griffin.interface.http.exposedItems.httpExposedDict import HttpExposedDict
from griffin.interface.http.exposedItems.httpExposedFloat import HttpExposedFloat
from griffin.interface.http.exposedItems.httpExposedInt import HttpExposedInt
from griffin.interface.http.exposedItems.httpExposedItem import HttpExposedItem
from griffin.interface.http.exposedItems.httpExposedList import HttpExposedList
from griffin.interface.http.exposedItems.httpExposedObject import HttpExposedObject
from griffin.interface.http.exposedItems.httpExposedString import HttpExposedString
from griffin.interface.http.server import Server
from griffin.interface.http.systemLogItem import SystemLogItem
from griffin.interface.http.wsprint import printGetPrinterElementsAction
from griffin.interface.http.wsprint import probeAction
from griffin.interface.http.wsprint import transferGetAction
from griffin.interface.http.wsprint.xmlSoapEndpoint import XmlSoapEndpoint

# Below imports are added to make it stand out these are 'dirty' imports from another service.
from griffin.led.ledService import LedService
from griffin.camera.cameraService import CameraService
from griffin.network.networkService import NetworkService
from griffin.nfc.nfcService import NfcService
from griffin.printer.printerService import PrinterService
from griffin.system.systemService import SystemService
from griffin.message.messageService import MessageService


## Ultimaker WebServices (REST API) implementation
#
#    @date December 2015
#
#    CUSTOM FORK - @date April 2022 - Added support for ring light changes
#
#
class UM3Server(Server):
    # Cache served files for a maximum of 5 minutes to prevent problems with our dns hijacking mechanism on the captive portal.
    FILE_CACHING_TIME = 5 * 60
    # File upload size limit in megabytes.
    FILE_UPLOAD_SIZE_LIMIT = 512

    API_BASE_PATH = "api"

    def __init__(self, import_name: str="Ultimaker", port: int=80, **kwargs) -> None:
        super().__init__(import_name, **kwargs)
        self._port = port

        self.__network_service = cast(NetworkService, dbusif.RemoteObject("network"))
        self.__network_service.connectSignal("modeChanged", self._onNetworkModeChanged)
        self.__system_service = cast(SystemService, dbusif.RemoteObject("system"))
        self.__printer_service = cast(PrinterService, dbusif.RemoteObject("printer"))
        self.__camera_service = cast(CameraService, dbusif.RemoteObject("camera"))
        self.__nfc_service = cast(NfcService, dbusif.RemoteObject("nfc"))
        self.__message_service = cast(MessageService, dbusif.RemoteObject("message"))

        self.__led_service = cast(LedService, dbusif.RemoteObject("led"))

        # Setup our special 404 handler for captive portal redirects.
        self.register_error_handler(404, self._handleFileNotFound)

        # Create the base path for all our API calls.
        api = HttpExposedItem(UM3Server.API_BASE_PATH, allowed_request_methods=[])
        api_v1 = HttpExposedItem("v1", api, allowed_request_methods=[])

        # Base object setup (printer, system, camera, materials, print_job)
        printer = HttpExposedObject("printer", api_v1)
        system = HttpExposedObject("system", api_v1)
        camera = Camera(self.__camera_service, "camera", api_v1)
        self._setupCameraInterface(camera)

        materials = Materials("materials", api_v1)
        print_job = PrintJob("print_job", api_v1)
        gcode = PrintJobGcode("gcode", print_job)
        job_container = PrintJobContainer("container", print_job)
        auth = AuthenticationAPI(self.__led_service, "auth", api_v1)
        Coffee(api_v1)

        debug = HttpExposedObject("debug", api_v1)
        marlin = HttpExposedObject("marlin", debug)
        HttpExposedDict("errors", marlin, get_function=self.__printer_service.debugErrorCounts)
        HttpExposedDict("success", marlin, get_function=self.__printer_service.debugSuccessCounts)

        # Printer exposure
        self._setupLedInterface(printer)
        self._setupHeadInterface(printer)

        bed             = HttpExposedObject("bed", printer)
        bed_temperature = HttpExposedObject("temperature", bed)
        pre_heat_bed = PreHeatBed("pre_heat", bed)
        current = HttpExposedFloat("current", bed_temperature, property_owner="printer/bed", property="current_temperature")
        target = HttpExposedFloat("target", bed_temperature, property_owner="printer/bed", property="target_temperature", put_function=lambda data: self.__setTargetTemperature("printer/bed", data))
        buildplate_type = HttpExposedString("type", bed, property_owner="printer/bed", property="buildplate_type")

        printer_status = HttpExposedString("status", printer, property_owner="printer", property="state")

        beep = Beep("beep", printer)
        diagnostics = Diagnostics("diagnostics", printer)

        # Network exposure
        network_state = HttpExposedObject("network", printer)
        self._setupWifiInterface(network_state)
        self._setupEthernetInterface(network_state)

        # GCode Header validation
        header_validation = ValidateGCodeHeader("validate_header", printer)

        # System exposure
        self._setupSystemInterface(system)

        # Print job exposure
        self._setupPrintJobInterface(print_job)

        self._setupHistoryInterfaces(api_v1)

        # Add certain objects as "root".
        self.addExposedObject(api_v1)

        # Only enable the WSPrint endpoint in developer mode right now, as it links to a non-functional driver.
        if self.__system_service.isDeveloperModeActive():
            self.__setupWSPrint()

        # Create the authentication service, this will generate create the DBus service that is used to communicate authentication requests with the rest of the system.
        AuthenticationService(auth)

    def main(self):
        self.registerAll()
        # Running FLASK standalone, replace starting a WSGI server with:
        #  self.run(port=80, threaded=True)
        WSGIServer(self).run()

    ## Create the WSPrint endpoint.
    #  This endpoint implements a limited WSPrint2.0 spec, which allows Windows 10 to discover the Ultimaker as printer and install the driver.
    #  The print job API is not supported.
    def __setupWSPrint(self):
        # Machine specific UUID
        uuid = self.__system_service.getMachineGUID()

        # Path part of the WSPrint endpoint. We can customize this as much as we like.
        wsprint_endpoint_name = "WSPrintEndpoint"

        # Create our discovery endpoint, this is a hardcoded path that allows other devices to discover us and know which endpoint to talk to.
        discovery_endpoint = XmlSoapEndpoint("StableWSDiscoveryEndpoint/schemas-xmlsoap-org_ws_2005_04_discovery")
        discovery_endpoint.addAction(probeAction.ProbeAction(uuid, wsprint_endpoint_name))
        self.addExposedObject(discovery_endpoint)

        # Our actual WSPrint endpoint. All actions are communicated to this endpoint.
        wsprint_endpoint = XmlSoapEndpoint(wsprint_endpoint_name)
        wsprint_endpoint.addAction(transferGetAction.TransferGetAction(uuid, wsprint_endpoint_name))
        wsprint_endpoint.addAction(printGetPrinterElementsAction.PrintGetPrinterElementsAction(uuid))

        self.addExposedObject(wsprint_endpoint)

    ## Setup the Led interface
    #  @param printer object  The printer object
    def _setupLedInterface(self, printer):
        case_led            = HttpExposedObject("led",       printer, allowed_request_methods=["GET", "PUT"])
        case_led_hue        = HttpExposedFloat("hue",        case_led,
            get_function = lambda: self.__led_service.getMainLightingHue(),
            put_function = lambda value: self.__led_service.setMainLightingHue(value),
            minimum=0,
            maximum=360
        )
        case_led_saturation = HttpExposedFloat("saturation", case_led,
            get_function=lambda: self.__led_service.getMainLightingSaturation(),
            put_function=lambda value: self.__led_service.setMainLightingSaturation(value),
            minimum=0,
            maximum=100
        )
        case_led_brightness = HttpExposedFloat("brightness", case_led,
            get_function=lambda: self.__led_service.getMainLightingBrightness(),
            put_function=lambda value: self.__led_service.setMainLightingBrightness(value),
            minimum=0,
            maximum=100
        )

        case_led_blink = Blink(self.__led_service, "blink", case_led)
        
        ring_led            = HttpExposedObject("ringled",       printer, allowed_request_methods=["GET", "PUT"])
        ring_led_hue        = HttpExposedFloat("hue",        ring_led,
            get_function = lambda: self.__led_service.getRingLightingHue(),
            put_function = lambda value: self.__led_service.setRingLightingHue(value),
            minimum=0,
            maximum=360
        )
        ring_led_saturation = HttpExposedFloat("saturation", ring_led,
            get_function=lambda: self.__led_service.getRingLightingSaturation(),
            put_function=lambda value: self.__led_service.setRingLightingSaturation(value),
            minimum=0,
            maximum=100
        )
        ring_led_brightness = HttpExposedFloat("brightness", ring_led,
            get_function=lambda: self.__led_service.getRingLightingBrightness(),
            put_function=lambda value: self.__led_service.setRingLightingBrightness(value),
            minimum=0,
            maximum=100
        )

    ## Setup the head interfaces
    #  @param printer object The printer object
    def _setupHeadInterface(self, printer):
        heads = HttpExposedList("heads", printer)
        head  = HttpExposedObject("0",   heads)

        head_position = HttpExposedObject("position", head, allowed_request_methods=["GET", "PUT"])
        self._setupAxisInterface(head_position, "x")
        self._setupAxisInterface(head_position, "y")
        self._setupAxisInterface(head_position, "z")

        # Marlin has no support for separate X and Y jerk, hence the XY_JERK for both of them.
        self._setupXYZInterface("jerk", head, "jerk_xy", "jerk_xy", "jerk_z", ["GET", "PUT"])
        self._setupXYZInterface("max_speed", head, "max_speed_x", "max_speed_y", "max_speed_z", ["GET", "PUT"])
        head_fan = HttpExposedFloat("fan", head, property_owner="printer/head/0", property="cooling_fan_speed")
        head_acceleration = HttpExposedFloat("acceleration", head, property="acceleration_xyz", allowed_request_methods=["GET", "PUT"])

        self._setupExtruderAndHotEndInterface(head)

    ## Setup the Axis interface of the head
    #  @param head_position object The head position
    #  @param axis string  The axis (x, y or z)
    def _setupAxisInterface(self, head_position, axis):
        head_axis = HttpExposedFloat(axis, head_position,
            get_function=lambda: self.__printer_service.getProcedureMetaData("MOVE_HEAD").get("current", {}).get(axis, float("nan")),
            put_function=lambda data: self.__printer_service.startProcedure("MOVE_HEAD", {axis: data})
        )

    ## Setup the extruders and hotends of the head
    #  @param head The head object
    def _setupExtruderAndHotEndInterface(self, head):
        extruder_list = HttpExposedList("extruders", head)

        hotend_count = self.__printer_service.getProperty("hotend_count")
        for index in range(0, hotend_count):
            extruder     = self._setupExtruderInterface(extruder_list, index)
            self._setupHotendInterface(extruder, index)

    ## Setup the nth Extruder interface and returns it
    #  @param extruder_list The available extruders
    #  @param index string The index for the extruders
    #  @return Returns single extruder object
    def _setupExtruderInterface(self, extruder_list, index):
        extruder            = HttpExposedObject(str(index), extruder_list)
        feeder              = HttpExposedObject("feeder", extruder)
        feeder_acceleration = HttpExposedFloat("acceleration", feeder, property="acceleration_e", allowed_request_methods=["GET", "PUT"])
        feeder_jerk         = HttpExposedFloat("jerk", feeder, property="jerk_e", allowed_request_methods=["GET", "PUT"])
        feeder_max_speed    = HttpExposedFloat("max_speed", feeder, property="max_speed_e", allowed_request_methods=["GET", "PUT"])
        active_material     = HttpExposedObject("active_material", extruder)
        length_remaining    = HttpExposedFloat("length_remaining", active_material,
                                               get_function=lambda: self.__nfc_service.getMaterialAmount(index)[0]  # Returns tuple of amounts (remaining, full)
                                               )
        material_guid       = HttpExposedString("guid", active_material, property_owner="printer/head/0/slot/%d" % (index), property="material_guid")
        # Keep the old upper case GUID endpoint until minimal 2017. This is to have a transitional period from internal Cura releases that use this endpoint.
        material_guid_old   = HttpExposedString("GUID", active_material, property_owner="printer/head/0/slot/%d" % (index), property="material_guid")
        return extruder

    ## Setup the Hotend interface the given extruder
    #  @param extruder object The extruder object
    #  @param index int The index of the hotend slot we are setting up.
    def _setupHotendInterface(self, extruder, index):
        property_owner = "printer/head/0/slot/%d" % (index)

        hotend = HttpExposedObject("hotend", extruder)
        self.__setupHotendStatistics(hotend, index)

        temperature = HttpExposedObject("temperature", hotend)
        current = HttpExposedFloat("current", temperature, property_owner=property_owner, property="current_temperature")
        target = HttpExposedFloat("target", temperature, property_owner=property_owner, property="target_temperature", put_function=lambda data: self.__setTargetTemperature(property_owner, data))
        cartridge_id = HttpExposedString("id", hotend, get_function=lambda: self.__printer_service.getHotendCartridgeProperty(index, "hotend_cartridge_id"))
        offset = HttpExposedObject("offset", hotend)

        hotend_slot_0 = dbusif.RemoteObject("printer", "printer/head/0/slot/%d" % (0))
        hotend_slot = dbusif.RemoteObject("printer", "printer/head/0/slot/%d" % (index))

        HttpExposedFloat("x", offset, get_function=lambda: hotend_slot.getProperty("x_offset") if hotend_slot.getProperty("x_offset") != "" else 0 + self.__printer_service.getProperty("hotend_offset_1_x"))
        HttpExposedFloat("y", offset, get_function=lambda: hotend_slot.getProperty("y_offset") if hotend_slot.getProperty("y_offset") != "" else 0 + self.__printer_service.getProperty("hotend_offset_1_y"))
        HttpExposedFloat("z", offset, get_function=lambda: hotend_slot.getProperty("z_height") - hotend_slot_0.getProperty("z_height") if hotend_slot.getProperty("z_height") != "" and hotend_slot_0.getProperty("z_height") != "" else 0)
        HttpExposedString("serial", hotend, get_function=lambda: hotend_slot.getProperty("serial_number"))

        # We only check if the X is set here, if the Z is not set that can be fine, as we are active leveling. And when the X is set, the Y is set for sure.
        HttpExposedString("state", offset, get_function=lambda: "invalid" if hotend_slot.getProperty("x_offset") == "" or hotend_slot.getProperty("z_height") == float("inf") or hotend_slot_0.getProperty("z_height") == float("inf") else "valid")

    ## Setup the hotend statistics interface
    #  @param hotend object The hotend object
    #  @param index int The index of the hotend slot we are setting up.
    def __setupHotendStatistics(self, hotend, index):
        statistics = HttpExposedObject("statistics", hotend)

        # From centimeters in hotend to millimeters
        HttpExposedInt("material_extruded", statistics, get_function=lambda: self.__printer_service.getHotendCartridgeProperty(index, "material_extruded") * 10 if self.__printer_service.getHotendCartridgeProperty(index, "material_extruded") != "" else 0)
        # From minutes in hotend to seconds
        HttpExposedInt("time_spent_hot", statistics, get_function=lambda: self.__printer_service.getHotendCartridgeProperty(index, "time_spend_hot") * 60 if self.__printer_service.getHotendCartridgeProperty(index, "time_spend_hot") != "" else 0)

        HttpExposedInt("max_temperature_exposed", statistics, get_function=lambda: self.__printer_service.getHotendCartridgeProperty(index, "max_exp_temperature") if self.__printer_service.getHotendCartridgeProperty(index, "max_exp_temperature") != "" else 0)
        HttpExposedString("last_material_guid",   statistics, get_function=lambda: self.__printer_service.getHotendCartridgeProperty(index, "last_material_guid"))

    ## Helper to create an XYZ object.
    #  @param local_path string The local file local_path
    #  @param parent object The parent object
    #  @param x_property string The name of the X procedure
    #  @param y_property string The name of the Y procedure
    #  @param z_property string The name of the Z procedure
    #  @param allowed_request_methods array The allowed methods to be used by the interface
    def _setupXYZInterface(self, local_path, parent, x_property, y_property, z_property, allowed_request_methods):
        xyz_object = HttpExposedObject(local_path, parent, allowed_request_methods=allowed_request_methods)
        x_object   = HttpExposedFloat("x", xyz_object, property=x_property, allowed_request_methods=None)
        y_object   = HttpExposedFloat("y", xyz_object, property=y_property, allowed_request_methods=None)
        z_object   = HttpExposedFloat("z", xyz_object, property=z_property, allowed_request_methods=None)

    ## Setup the Wifi interface
    #  @param network_state object The network state object
    def _setupWifiInterface(self, network_state):
        wifi = HttpExposedObject("wifi", network_state)
        path = "/nl/ultimaker/network"
        name = "network"
        wifi_connected = HttpExposedBool("connected", wifi, get_function=lambda: dbusif.RemoteObject(name, path).getConnectedMethod() in ["WIFI", "HOTSPOT"])
        wifi_enabled   = HttpExposedBool("enabled",   wifi, get_function=lambda: dbusif.RemoteObject(name, path).getMode() in ["AUTO", "HOTSPOT", "WIFI SETUP", "WIRELESS"])
        wifi_mode      = HttpExposedString("mode",    wifi, get_function=lambda: dbusif.RemoteObject(name, path).getMode())
        wifi_ssid      = HttpExposedString("ssid",    wifi, get_function=lambda: dbusif.RemoteObject(name, path).getHotspotSSID())
        wifi_networks = Wifi(self.__network_service, "wifi_networks", network_state)

    ## Setup the Ethernet interface
    #  @param network_state  object The network state object
    def _setupEthernetInterface(self, network_state):
        ethernet           = HttpExposedObject("ethernet", network_state)
        ethernet_connected = HttpExposedBool("connected",  ethernet, get_function=lambda: self.__network_service.getConnectedMethod() in ["ETHERNET"])
        ethernet_enabled   = HttpExposedBool("enabled",    ethernet, get_function=lambda: self.__network_service.getMode() in ["AUTO", "CABLE"])

    ## Setup the System interface(s)
    #  @param system object The system object
    def _setupSystemInterface(self, system):
        platform    = HttpExposedString("platform", system, get_function=lambda: self.__system_service.getPlatform())
        uptime      = HttpExposedInt(   "uptime",   system, get_function=lambda: self.__system_service.getUptime())
        hostname    = HttpExposedString("hostname", system, get_function=lambda: self.__system_service.getHostName())
        system_name = HttpExposedString("name",     system,
            get_function=lambda: self.__system_service.getMachineName(),
            put_function=lambda data: self.__system_service.setMachineName(data)
        )
        guid        = HttpExposedString("guid", system, get_function=lambda: self.__system_service.getMachineGUID())
        firmware    = Firmware("firmware", system)

        memory       = HttpExposedObject("memory", system)
        memory_used  = HttpExposedInt("used",  memory, get_function=lambda: self.__system_service.getMemoryUsage()[0])
        memory_total = HttpExposedInt("total", memory, get_function=lambda: self.__system_service.getMemoryUsage()[1])

        HttpExposedString("type", system, get_function=lambda: "3D printer")
        HttpExposedString("variant", system, get_function=lambda: self.__printer_service.getProperty("machine_type_name"))

        hardware     = HttpExposedObject("hardware", system)
        HttpExposedInt("typeid", hardware, get_function=lambda: self.__system_service.getMachineBOM()[0])
        HttpExposedInt("revision", hardware, get_function=lambda: self.__system_service.getMachineBOM()[1])

        # Some clarification on the log; The system get log call returns a dbus array with strings.
        # We use some python comprehension fu to mash it into a single string.
        log      = SystemLogItem("log",          system)
        language = HttpExposedString("language", system, get_function=lambda: self.__system_service.getLanguage())
        country  = HttpExposedString("country",  system,
            get_function=lambda: self.__system_service.getCountry(),
            put_function=lambda data: self.__system_service.setCountry(data)
        )

        # This section sets up the endpoints to show a message to the user, for instance from the cluster.
        message_screen = MessageScreen("display_message", system, message_service=self.__message_service)

        time_node = HttpExposedObject("time", system)
        HttpExposedFloat("utc", time_node,
            get_function=lambda: self.__system_service.getUTCSystemTime(),
            put_function=lambda data: self.__system_service.setUTCSystemTime(data)
        )

    ## Setup the Camera interface(s)
    #  @param camera object The camera object
    def _setupCameraInterface(self, camera):
        HttpExposedString("feed", camera, get_function=lambda: "http://%s:8080/?action=stream" % urlparse(flask.request.url).hostname)

    ## Setup the PrintJob interface
    #  @param print_job object The print job object
    def _setupPrintJobInterface(self, print_job):
        print_job_name         = HttpExposedString("name",      print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PRINT").get("jobname", ""))
        print_job_time_elapsed = HttpExposedInt("time_elapsed", print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PRINT").get("time_elapsed", 0))
        print_job_time_total   = HttpExposedInt("time_total",   print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PRINT").get("time_total", 0))
        print_job_progress     = HttpExposedFloat("progress",   print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PRINT").get("progress", 0))
        print_job_uuid         = HttpExposedString("uuid",      print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PRINT").get("uuid", ""))

        print_job_datetime_started = HttpExposedDatetime("datetime_started", print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PRINT").get("datetime_started", 0))
        print_job_datetime_finished= HttpExposedDatetime("datetime_finished", print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PRINT").get("datetime_finished", 0))
        print_job_datetime_cleaned \
                               = HttpExposedDatetime("datetime_cleaned", print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PRINT").get("datetime_cleaned", 0))

        print_job_origin       = HttpExposedString("source",    print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PRINT").get("origin", ""))
        print_job_origin_user  = HttpExposedString("source_user", print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PRINT").get("origin_user", "") if self.__printer_service.getProcedureMetaData("PRINT").get("origin", "") == "WEB_API" else "")
        print_job_origin_application = HttpExposedString("source_application", print_job, get_function=lambda:  self.__printer_service.getProcedureMetaData("PRINT").get("origin_application", "") if self.__printer_service.getProcedureMetaData("PRINT").get("origin", "") == "WEB_API" else "")

        print_job_state        = PrintJobState("state",         print_job)
        print_job_pause_source = HttpExposedString("pause_source", print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PAUSE_PRINT").get("source", "unknown") if self.__printer_service.getProperty("job_state") in ["pausing", "paused"] else "")
        print_job_result       = HttpExposedString("result",    print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PRINT").get("result", ""))
        print_job_reprint_original_uuid = HttpExposedString("reprint_original_uuid",    print_job, get_function=lambda: self.__printer_service.getProcedureMetaData("PRINT").get("reprint_original_uuid", ""))

    ## Set up the end point for PrintJobHistory
    # @param parent the parent of this endpoint, which dictates the URL of this endpoint
    def _setupHistoryInterfaces(self, parent):
        history = HttpExposedObject("history", parent)
        print_job_history = PrintJobHistory("print_jobs", history)
        event_history = EventHistory("events", history)

    ## Handle a 404 (file not found) error
    #  We use custom error handling for file not founds on the printer.
    #  This to generate captive portal functionality, which need a 302 redirect on a file not found.
    #  Every file-not-found error, that is NOT part of the API will redirect to the root
    #  location of the server. API 404's are handled normally.
    #  @param exception that is given for this error.
    def _handleFileNotFound(self, exception):
        # When the requested URL is part of the API, generate the standard json error.
        if flask.request.path == "/" or flask.request.path.startswith("/%s" % (UM3Server.API_BASE_PATH)):
            return self._createJSONError(exception)
        # Else, redirect to the root folder.
        return flask.redirect("/", 302)

    ## Callback when the network service changes mode.
    #  When we switch to wifi setup, we want to disable authentication. Else authentication should be enabled.
    #  @param new_mode New network mode from the network service.
    def _onNetworkModeChanged(self, new_mode):
        if new_mode == "WIFI SETUP":
            self.getAuthenticationController().disableAuthentication()
        else:
            self.getAuthenticationController().enableAuthentication()

    ## Helper function to set a target temperature.
    #  The printer service does not allow you to set the target temperature of the printer, you need to set the pre_tune_target_temperature.
    #  This function calculates the proper pre_tune_target_temperature to make get the resulting target temperature.
    #  @param property_owner The dbus path of the printer service that needs to be accessed. Example "printer/head/0/slot/0" or "printer/bed"
    #  @param new_temperature The new target temperature for the heatable object (bed/hotend). 0 is a special case and means the heater should be off and cool down.
    def __setTargetTemperature(self, property_owner, new_temperature):
        owner = dbusif.RemoteObject("printer", property_owner)
        # Do not apply the tuning offset on a target of 0, as 0 is the special case where we request things to be off.
        if new_temperature != 0.0:
            new_temperature -= owner.getProperty("tune_offset_temperature")
        return owner.setProperty("pre_tune_target_temperature", new_temperature)


class Coffee(HttpExposedObject):
    def __init__(self, parent):
        super().__init__("coffee", parent)

    def get(self):
        return flask.Response(flask.json.dumps({"Reply": "I'm a Little Teapot"}), status=418, mimetype="application/json")
