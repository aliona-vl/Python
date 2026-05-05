class MusicalInstrument:
     def __init__(self, name, instrument_type):
        self.name = name
        self.instrument = instrument_type


    def play(self):
        pass

    def get_fact(self):
        return f"The {self.name} is part of the {self.instrument_type} family of instruments."
instrument_1.play()
instrument_1 = MusicalInstrument("Oboe", "woodwind")
instrument_2 = MusicalInstrument("Trumpet","brass")

print(instrument_1.instrument_type)
print(instrument_1.name)
print(instrument_2.instrument_type)
print(instrument_2.name)