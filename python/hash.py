class HashTable:
    def __init__(self):
        # Initialisierung der Sammlung als leeres Dictionary
        self.collection = {}

    def hash(self, key):
        # Summiert die Unicode-Werte (ord) jedes Zeichens im String
        return sum(ord(char) for char in key)

    def add(self, key, value):
        hash_value = self.hash(key)
        
        # Falls der Hash-Wert noch nicht existiert, erstelle ein neues Unter-Dictionary
        if hash_value not in self.collection:
            self.collection[hash_value] = {}
        
        # Speichere das Key-Value-Paar im Unter-Dictionary (behandelt Kollisionen)
        self.collection[hash_value][key] = value

    def remove(self, key):
        hash_value = self.hash(key)
        
        # Überprüfe, ob der Hash-Index existiert und ob der spezifische Key darin liegt
        if hash_value in self.collection and key in self.collection[hash_value]:
            del self.collection[hash_value][key]
            
            # Optional: Wenn das Unter-Dictionary leer ist, können wir den Index ganz löschen
            if not self.collection[hash_value]:
                del self.collection[hash_value]

    def lookup(self, key):
        hash_value = self.hash(key)
        
        # Greife auf den Hash-Index zu und schaue im Unter-Dictionary nach
        if hash_value in self.collection:
            return self.collection[hash_value].get(key, None)
        
        return None