from . import abstractController
from ..effects import staticEffect
from ..effects import fadeEffect
from ..effects import glowEffect
from griffin import dbusif
from .. import colorTheme
from griffin.math.immutableHsvColor import ImmutableHsvColor


## The ButtonRing controller
#  This LED controller has the following features:
#  * Glow the LEDs when there is an error.
#  * Glow the LEDs when a print if finished and needs to be removed.
#  * Glow the LEDs when a print is paused.
#  * Startup with a fade-in
#  * Always use the pre-defined ULTIMAKER color, which is blueish
class ButtonRingController(abstractController.AbstractController):
    # Fade in time at startup.
    __STARTUP_FADE_TIME = 6.0

    def __init__(self, hardware):
        super().__init__(hardware)

        # Start up by fading from black to the main color in
        self._queueEffect(fadeEffect.FadeEffect(colorTheme.ULTIMAKER, self.__STARTUP_FADE_TIME))

        printer = dbusif.RemoteObject("printer")
        self.__printer_state = printer.getProperty("state")
        self.__job_state = printer.getProperty("job_state")
        self.__interaction_required = printer.getProperty("interaction_required")
        printer.connectSignal("propertyChanged", self.__onPrinterPropertyChanged)
        self.__customTheme = colorTheme.ULTIMAKER.copy()
        

    # Function called by the onPropertyChanged signal of the printer dbus remoteobject.
    # @param property_key, key of the changed property
    # @param value, new value of the property
    def __onPrinterPropertyChanged(self, property_key, value):
        # Check if one of our monitored properties change and
        if property_key == "state":
            self.__printer_state = value
            self.__update()
        if property_key == "job_state":
            self.__job_state = value
            self.__update()
        if property_key == "interaction_required":
            self.__interaction_required = value
            self.__update()

    ## Update the leds according to the state of the printer.
    #
    def __update(self):
        if self.__printer_state == "error":
            self._queueEffect(glowEffect.GlowEffect(colorTheme.BLACK, colorTheme.RED, 0.5))
        elif self.__interaction_required:
            self._queueEffect(glowEffect.GlowEffect(colorTheme.BLACK, colorTheme.YELLOW, 0.5))
        elif self.__printer_state == "printing" and self.__job_state == "wait_cleanup":
            self._queueEffect(glowEffect.GlowEffect(colorTheme.BLACK, colorTheme.PURPLE, 0.5))
        elif self.__printer_state == "printing" and self.__job_state == "paused":
            self._queueEffect(glowEffect.GlowEffect(colorTheme.BLACK, colorTheme.CYAN, 0.5))
        else:
            #self._queueEffect(staticEffect.StaticEffect(colorTheme.ULTIMAKER))
            self._queueEffect(staticEffect.StaticEffect(self.__customTheme))
    
    def getCustomRingHue(self):
        return self.__customTheme.hue
        
    def getCustomRingSaturation(self):
        return self.__customTheme.saturation
        
    def getCustomRingBrightness(self):
        return self.__customTheme.value
        
    def setCustomRingHue(self, hue):
        self.__customTheme.hue = hue
        self.__update()
        
    def setCustomRingSaturation(self, saturation):
        self.__customTheme.saturation = saturation
        self.__update()
    
    def setCustomRingBrightness(self, brightness):
        self.__customTheme.value = brightness
        self.__update()
