import logging

from griffin import dbusif
from griffin.preferences import registryFile
from griffin.printer.properties.propertyContainer import PropertyContainer
from griffin.printer.serviceWithProperties import DBusServiceWithProperties
from .controllers import backLightController
from .controllers import buttonRingController
from .controllers import headSlotController
from .controllers import mainLightingController
from griffin.led.hardware.backLightHardware import BackLightHardware
from griffin.led.hardware.buttonRingHardware import ButtonRingHardware
from griffin.led.hardware.debugLedsHardware import DebugLedsHardware
from griffin.led.hardware.dummyHardware import DummyHardware
from griffin.led.hardware.headSlotHardware import HeadSlotHardware
from griffin.led.hardware.mainLightingHardware import MainLightingHardware

log = logging.getLogger(__name__.split(".")[-1])


# Interface to the LEDs attached to the system.
# Currently talks to the printer service, as it needs to control the leds through the
# GCode interface with M142 commands.
class LedService(DBusServiceWithProperties):
    __LIGHTING_CONTROLLERS_PREFERENCES_FILE = "main_lighting.json"

    def __init__(self):
        self.__property_container = PropertyContainer()
        super().__init__(self.__property_container, "led")
        # Simple flag to see if we are running already.
        self.__running = False
        # Reference to the main controller, we provide an interface to the main controller,
        # So we need this reference.
        self.__main_controller = None
        
        self.__button_ring_controller = None

        self.__printer = dbusif.RemoteObject("printer")
        self.__printer.connectSignal("propertyChanged", self.__onPrinterPropertyChanged)

        self.__system = dbusif.RemoteObject("system")
        ## Add callback to dbus printer service which implements the call to the resetToFactorySettings
        self.__system.addFactoryResetCallback("led", "resetSettings")

        self.__debugLeds = DebugLedsHardware()

        # Directly call the state change event, so the leds work even if missed the post booting event.
        # Do this as the last init-statement! To ensure all encountered variables are initialized.
        self._onPrinterStateChanged(self.__printer.getProperty("state"))

    ## The callback function to register which needs to be called when a factory reset is issued
    #  @param reset_type One of the defined values from FactoryReset which would indicate a hard or soft reset
    @dbusif.method("s", "")
    def resetSettings(self, reset_type):
        log.info("Factory reset execution")
        if self.__main_controller is not None:
            self.__main_controller.resetToDefaultValues()

    # @param hue: float, range 0 to 360, sets the hue of the leds, which dictates which color the frame lights become.
    #   Note: If the printer does not support color, this function does nothing.
    @dbusif.method("d", "")
    def setMainLightingHue(self, hue):
        if self.__main_controller is None:
            return
        self.__main_controller.setMainColorHue(hue)

    # @return float: The hue, range 0 to 360, which dictates which color the frame lights become.
    #   Note: If the printer does not support color, this function does nothing.
    @dbusif.method("", "d")
    def getMainLightingHue(self):
        if self.__main_controller is None:
            return 0.0
        return self.__main_controller.getMainColorHue()

    # @param saturation: float, range 0 to 100, which dictates how much color is in the lighting.
    #       0 saturation = no color
    #     100 saturation = full color
    @dbusif.method("d", "")
    def setMainLightingSaturation(self, saturation):
        if self.__main_controller is None:
            return
        self.__main_controller.setMainColorSaturation(saturation)

    # @return float: The saturation, range 0 to 100, which dictates how much color is in the lighting.
    #       0 saturation = no color
    #     100 saturation = full color
    @dbusif.method("", "d")
    def getMainLightingSaturation(self):
        if self.__main_controller is None:
            return 0.0
        return self.__main_controller.getMainColorSaturation()

    # @param brightness: float, sets the API set brightness in the range 0 to 100 (volatile)
    @dbusif.method("d", "")
    def setMainLightingBrightness(self, brightness):
        if self.__main_controller is None:
            return
        self.__main_controller.setMainColorBrightness(brightness)

    # @return returns the API set brightness in the range 0 to 100 as a float (volatile)
    @dbusif.method("", "d")
    def getMainLightingBrightness(self):
        if self.__main_controller is None:
            return 0.0
        return self.__main_controller.getMainColorBrightness()
        
        
    
    # ---- BEGIN CUSTOM FORK ----
    
    
    
    # @param hue: float, range 0 to 360, sets the hue of the leds, which dictates which color the frame lights become.
    #   Note: If the printer does not support color, this function does nothing.
    @dbusif.method("d", "")
    def setRingLightingHue(self, hue):
        if self.__button_ring_controller is None:
            return
        self.__button_ring_controller.setCustomRingHue(hue)

    # @return float: The hue, range 0 to 360, which dictates which color the frame lights become.
    #   Note: If the printer does not support color, this function does nothing.
    @dbusif.method("", "d")
    def getRingLightingHue(self):
        if self.__button_ring_controller is None:
            return 0.0
        return self.__button_ring_controller.getCustomRingHue()

    # @param saturation: float, range 0 to 100, which dictates how much color is in the lighting.
    #       0 saturation = no color
    #     100 saturation = full color
    @dbusif.method("d", "")
    def setRingLightingSaturation(self, saturation):
        if self.__button_ring_controller is None:
            return
        self.__button_ring_controller.setCustomRingSaturation(saturation)

    # @return float: The saturation, range 0 to 100, which dictates how much color is in the lighting.
    #       0 saturation = no color
    #     100 saturation = full color
    @dbusif.method("", "d")
    def getRingLightingSaturation(self):
        if self.__button_ring_controller is None:
            return 0.0
        return self.__button_ring_controller.getCustomRingSaturation()

    # @param brightness: float, sets the API set brightness in the range 0 to 100 (volatile)
    @dbusif.method("d", "")
    def setRingLightingBrightness(self, brightness):
        if self.__button_ring_controller is None:
            return
        self.__button_ring_controller.setCustomRingBrightness(brightness)

    # @return returns the API set brightness in the range 0 to 100 as a float (volatile)
    @dbusif.method("", "d")
    def getRingLightingBrightness(self):
        if self.__button_ring_controller is None:
            return 0.0
        return self.__button_ring_controller.getCustomRingBrightness()
    
    
    
    # ---- END CUSTOM FORK ----
    
    

    # @param brightness: float, sets the user configured brightness in the range 0 to 100 (non volatile)
    @dbusif.method("d", "")
    def setMainLightingUserBrightness(self, brightness):
        # TODO: EM-2239 [New] - use properties for lightning in jedi-display
        log.warning("setMainLightingUserBrightness is deprecated, please use the user_brightness property instead")
        if self.__main_controller is None:
            return
        self.__main_controller.setUserBrightness(brightness)

    # @return returns the user configured brightness in the range 0 to 100 as a float (non volatile)
    @dbusif.method("", "d")
    def getMainLightingUserBrightness(self):
        # TODO: EM-2239 [New] - use properties for lightning in jedi-display
        log.warning("getMainLightingUserBrightness is deprecated, please use the user_brightness property instead")
        if self.__main_controller is None:
            return 0.0
        return self.__main_controller.getUserBrightness()

    ## Set a main lighting mode flag.
    #  The mode flags are boolean on/off flags that control behavior of the main lighting.
    # @param flag_name string: See controller.mainLightingController for options.
    # @param new_mode bool: True if this mode needs to be enabled, false if not.
    @dbusif.method("sb", "")
    def setMainLightingModeFlag(self, flag_name, new_mode):
        # TODO: EM-2239 [New] - use properties for lightning in jedi-display
        log.warning("setMainLightingModeFlag is deprecated, please use corresponding property instead")
        if self.__main_controller is None:
            return
        self.__main_controller.setModeFlag(flag_name, new_mode)

    ## Get a main lighting mode flag.
    #  The mode flags are boolean on/off flags that control behavior of the main lighting.
    # @param flag_name string: See controller.mainLightingController for options.
    # @return bool: True if this mode is enabled, false if not.
    @dbusif.method("s", "b")
    def getMainLightingModeFlag(self, flag_name):
        # TODO: EM-2239 [New] - use properties for lightning in jedi-display
        log.warning("getMainLightingModeFlag is deprecated, please use corresponding property instead")
        if self.__main_controller is None:
            return False
        return self.__main_controller.getModeFlag(flag_name)

    ## Set a main lighting runtime flag.
    #  The mode flags are boolean on/off flags that control behavior of the main lighting.
    # @param flag_name string: See controller.mainLightingController for options.
    # @param new_mode bool: True if this mode needs to be enabled, false if not.
    @dbusif.method("sb", "")
    def setMainLightingRuntimeFlag(self, flag_name, new_mode):
        if self.__main_controller is None:
            return
        self.__main_controller.setRuntimeFlag(flag_name, new_mode)

    ## Get a main lighting runtime flag.
    #  The mode flags are boolean on/off flags that control behavior of the main lighting.
    # @param flag_name string: See controller.mainLightingController for options.
    # @return bool: True if this mode is enabled, false if not.
    @dbusif.method("s", "b")
    def getMainLightingRuntimeFlag(self, flag_name):
        if self.__main_controller is None:
            return False
        return self.__main_controller.getRuntimeFlag(flag_name)

    ## This function causes the main leds to blink [count] times at the selected frequency.
    # @param frequency: float, frequency of blinking
    # @param count: int > 0, amount of blinks
    @dbusif.method("dd", "")
    def blinkMainLighting(self, frequency, count):
        if self.__main_controller is None:
            return
        self.__main_controller.blink(frequency, count)

    ## Simple function to handle turning on and off the debug leds
    #  @param led The led name (ol1, ol2 or ol3)
    #  @param toggle True if to to turn on, False to turn off
    @dbusif.method("sb", "")
    def debugLed(self, led, toggle):
        if toggle:
            self.__debugLeds.turnOn(led)
        else:
            self.__debugLeds.turnOff(led)

    ## Callback from the printer property change
    #  @param key: string, name of the property
    #  @param value: variant, new value of the property
    def __onPrinterPropertyChanged(self, key, value):
        if key == "state":
            self._onPrinterStateChanged(value)

    ## Private printer state changed event, called from the property change of the printer service.
    #  @param state: string containing the new printer state.
    def _onPrinterStateChanged(self, state):
        if state != "booting":
            self.__start()

    ## Start the led service
    #  this is done when the printer is no longer booting.
    #  It creates our controllers with the hardware instances.
    def __start(self):
        if self.__running:
            return
        self.__running = True

        # self.__property_container is passed to the controller here to add it's properties to
        # The property controller needs to be passed to our super().__init__, but the controller is only instantiated
        # at startup, so waaaay after that __init__.
        # Instead, we pass the container so that the controller can add properties when we construct it below

        preferences = registryFile.RegistryFile(self.__LIGHTING_CONTROLLERS_PREFERENCES_FILE)
        self.__main_controller = mainLightingController.MainLightingController(
            MainLightingHardware(),
            self.__property_container, preferences
        )

        self.__back_light_controller = backLightController.BackLightController(
            BackLightHardware(), 
            self.__property_container, preferences
        )
        
        machine_bom = self.__system.getMachineBOM()
        
        # Only enable buttonring leds for the UM3 and UM3 extended.
        # TODO: Availability settings SHOULD come from json configuration file, which is disclosed in the 
        # Controller class, which cannot be imported into this service.
        # Probably because of (indirect) circular dependencies.
        if str(machine_bom[0]) in ["9066", "9511"]:  # Resp. UM3 and UM3 Extended
            self.__button_ring_controller = buttonRingController.ButtonRingController(ButtonRingHardware())
        else :
            buttonRingController.ButtonRingController(DummyHardware())

        headSlotController.HeadSlotController(0, HeadSlotHardware(0))
        headSlotController.HeadSlotController(1, HeadSlotHardware(1))
