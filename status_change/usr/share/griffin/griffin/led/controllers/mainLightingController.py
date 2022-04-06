from typing import Dict

from griffin.printer.properties.nonVolatileProperty import NonVolatileProperty
from griffin.printer.properties.propertyContainer import PropertyContainer
from ..hardware import abstractHardware
from . import abstractController
from ..effects import staticEffect
from ..effects import fadeEffect
from ..effects import blinkEffect
from ..effects import glowEffect
from .. import colorTheme

from griffin.preferences import registryFile
from griffin.math import hsvColor
from griffin import dbusif
from griffin import timer

import random
import logging

log = logging.getLogger(__name__.split(".")[-1])


## The MainLighting controller
#  This LED controller has the following features:
#  * A HSV color can be set, and each of the HSV parameters can be set separately.
#  * It has a control mode, which defines the relation to the LEDs and the system state.
#  * It can be asked to blink once to notify the user.
#  * A non volatile maximum brightness can be configured.
#  * At boot up, the LEDs fade from off to the requested state.
class MainLightingController(abstractController.AbstractController):
    ## Mode to glow the printer on/off when the print is finished and the printer needs to be emptied.
    __MODE_FLAG_GLOW_WHEN_FINISHED = "glow_when_print_is_finished"
    ## Mode to have the lighting only on when printing, and off while idle.
    __MODE_FLAG_ON_WHEN_PRINTING = "on_when_printing"
    ## Party mode, cycle between colors.
    __MODE_FLAG_PARTY = "party"

    ## Runtime flag to glow the printer on/off during authentication request.
    RUNTIME_FLAG_AUTHENTICATING = "authenticating"
    ## Runtime flag to glow when we have a API driven message on the screen.
    RUNTIME_FLAG_MESSAGE = "message"

    __ALL_MODE_FLAGS = (__MODE_FLAG_GLOW_WHEN_FINISHED, __MODE_FLAG_ON_WHEN_PRINTING, __MODE_FLAG_PARTY)
    __ALL_RUNTIME_FLAGS = (RUNTIME_FLAG_AUTHENTICATING, RUNTIME_FLAG_MESSAGE)

    # Default value for each of the modes. A fresh setup forces the modes to these values.
    __DEFAULT_MODE_SETTINGS = {
        __MODE_FLAG_GLOW_WHEN_FINISHED: True,
        __MODE_FLAG_ON_WHEN_PRINTING: False,
        __MODE_FLAG_PARTY: False,
    }

    __STARTUP_FADE_TIME = 6.0
    __DEFAULT_GLOW_FREQUENCY = 0.1
    __DARK_COLOR_BRIGHTNESS_FACTOR = 0.3

    ## Set up a controller for the main lights of the printer
    # @param hardware the interface to the hardware to be controller
    # @param property_container the controller will add some properties to the container.
    # @param preferences the configuration settings
    def __init__(self, hardware: abstractHardware.AbstractHardware, property_container: PropertyContainer, preferences: registryFile.RegistryFile) -> None:
        super().__init__(hardware)

        self.__property_container = property_container

        # Configuration data (non volatile)
        self.__preferences = preferences
        for key in self.__ALL_MODE_FLAGS:
            if not self.__preferences.has(key):
                self.__preferences.setAsBoolean(key, self.__DEFAULT_MODE_SETTINGS[key])
            prop = NonVolatileProperty(self.__preferences, key, self.__preferences.getAsBoolean(key))
            prop.onChange.connect(lambda property, value: self.__update())
            self.__property_container.addProperty(prop)

        if not self.__preferences.has("user_brightness"):
            self.__preferences.setAsFloat("user_brightness", 100.0)
        prop = NonVolatileProperty(self.__preferences, "user_brightness", self.__preferences.getAsFloat("user_brightness"))
        prop.onChange.connect(lambda property, value: self.__update())
        self.__property_container.addProperty(prop)

        # Runtime data (volatile)
        self.__main_color = colorTheme.WHITE.copy()
        self.__runtime_flags = {}  # type: Dict[str, bool]

        printer = dbusif.RemoteObject("printer")
        self.__printer_state = printer.getProperty("state")
        self.__job_state = printer.getProperty("job_state")
        printer.connectSignal("propertyChanged", self.__onPrinterPropertyChanged)

        # Start up by fading from black to the main color in
        self._queueEffect(fadeEffect.FadeEffect(self.__getColor(), self.__STARTUP_FADE_TIME))

    ## Resets to default values
    def resetToDefaultValues(self):
        for key in self.__ALL_MODE_FLAGS:
            self.__preferences.setAsBoolean(key, self.__DEFAULT_MODE_SETTINGS[key])
        self.__preferences.setAsFloat("user_brightness", 100.0)
        self.__preferences.forceSave()

    def setMainColorHue(self, hue):
        self.__main_color.hue = hue
        self.__update()

    def setMainColorSaturation(self, saturation):
        self.__main_color.saturation = saturation
        self.__update()

    def setMainColorBrightness(self, brightness):
        self.__main_color.value = brightness
        self.__update()

    def getMainColorHue(self):
        return self.__main_color.hue

    def getMainColorSaturation(self):
        return self.__main_color.saturation

    def getMainColorBrightness(self):
        return self.__main_color.value

    def blink(self, frequency=1.0, count=1):
        self._queueEffect(blinkEffect.BlinkEffect(self.__getColor(), frequency, count))

    ## Set a boolean mode flag for the main lighting controller.
    #  @param flag_name string from the __ALL_MODE_FLAGS list.
    #  @param new_value bool True if this mode needs to be enabled, false if not.
    def setModeFlag(self, flag_name: str, new_value: bool) -> None:
        assert flag_name in self.__ALL_MODE_FLAGS
        self.__property_container.setPropertyValue(flag_name, new_value)
        self.__update()

    ## Set a boolean mode flag for the main lighting controller.
    #  @param flag_name string from the __ALL_MODE_FLAGS list.
    #  @return bool True if this mode is enabled, false if not.
    def getModeFlag(self, flag_name: str) -> bool:
        assert flag_name in self.__ALL_MODE_FLAGS
        return self.__property_container.get(flag_name).get()

    ## Set a boolean mode flag for the main lighting controller.
    #  @param flag_name string from the __ALL_RUNTIME_FLAGS list.
    #  @param new_value bool True if this mode needs to be enabled, false if not.
    def setRuntimeFlag(self, flag_name, new_value):
        assert flag_name in self.__ALL_RUNTIME_FLAGS
        self.__runtime_flags[flag_name] = new_value
        self.__update()

    ## Set a boolean mode flag for the main lighting controller.
    #  @param flag_name string from the __ALL_RUNTIME_FLAGS list.
    #  @return bool True if this mode is enabled, false if not.
    def getRuntimeFlag(self, flag_name):
        assert flag_name in self.__ALL_RUNTIME_FLAGS
        return self.__runtime_flags.get(flag_name, False)

    # Set the user configured brightness
    # @param user_brightness Float in the range 0 to 100
    def setUserBrightness(self, user_brightness: float):
        user_brightness = max(0.0, min(user_brightness, 100.0))
        self.__property_container.setPropertyValue("user_brightness", user_brightness)

    # @return user_brightness: Float in the range 0 to 100
    def getUserBrightness(self) -> float:
        return self.__property_container.get("user_brightness").get()

    def getPropertyContainer(self) -> PropertyContainer:
        return self.__property_container

    # Function called by the onPropertyChanged signal of the printer dbus remoteobject.
    # @param property_key key of the changed property
    # @param value new value of the property
    def __onPrinterPropertyChanged(self, property_key, value):
        # Check if one of our monitored properties change and
        if property_key == "state":
            self.__printer_state = value
            self.__update()
        if property_key == "job_state":
            self.__job_state = value
            self.__update()
            
# --- BEGIN CHANGES ---            
            
    ## Update the leds according to the state of the printer.
    def __update(self):
        if self.getModeFlag(self.__MODE_FLAG_PARTY):
            if not self._hasEffectInQueue():
                self._queueEffect(fadeEffect.FadeEffect(hsvColor.HsvColor(random.uniform(0, 360), 100, self.__property_container.get("user_brightness").get()), 2.0))
                timer.Timer("PARTY!", 2.0, self.__update).start()
        elif self.getRuntimeFlag(self.RUNTIME_FLAG_AUTHENTICATING) or self.getRuntimeFlag(self.RUNTIME_FLAG_MESSAGE):
            self._queueEffect(glowEffect.GlowEffect(self.__getColor(), self.__getDarkColor(), self.__DEFAULT_GLOW_FREQUENCY))
        elif self.__stateIsWaitingForCleanup():
            self._queueEffect(staticEffect.StaticEffect(colorTheme.PURPLE))
        elif self.__stateIsPrinting():
            self._queueEffect(staticEffect.StaticEffect(colorTheme.CYAN))
        elif self.__stateIsMaintenance():
            self._queueEffect(staticEffect.StaticEffect(colorTheme.YELLOW))
        elif self.__stateIsError():
            self._queueEffect(staticEffect.StaticEffect(colorTheme.RED))
        else:
            self._queueEffect(staticEffect.StaticEffect(colorTheme.GREEN))

    def __stateIsPrinting(self):
        return self.__printer_state == "printing"
        
    def __stateIsError(self):
        return self.__printer_state == "error"
        
    def __stateIsMaintenance(self):
        return self.__printer_state == "maintenance"
    
# --- END CHANGES ---

    def __stateIsWaitingForCleanup(self):
        return self.__printer_state == "printing" and (self.__job_state == "wait_cleanup" or self.__job_state == "none")

    ## Get the current color requested by the user and limited by the configured maximum brightness.
    #  @return HsvColor object.
    def __getColor(self):
        return hsvColor.HsvColor(self.__main_color.hue, self.__main_color.saturation, self.__main_color.value * self.__property_container.get("user_brightness").get() / 100.0)

    # @return HsvColor object which is a darker version of the user set color returned by __getColor.
    def __getDarkColor(self):
        color = self.__getColor()
        color.value *= self.__DARK_COLOR_BRIGHTNESS_FACTOR
        return color
