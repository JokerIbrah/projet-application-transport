# projet-application-transport

Je veux essayer de faire une application pour développé mes scrills en dév

Je vais faire une application minimaliste avec le maximum d'outil pour les usagers des transports en commun du réseau messin (Le Met')

Comme dans la création d'un site web, je vais commencer par le statique mais à terme je vais utilisé pour cela le GTFS-RT du réseau Le Met' pour pouvoir avoir des informations en temps réels des retards ce que le GTFS statique ne permet pas de savoir

Dans le fichier loader ne décisions devait être prise:
Privilègié l'espace disque ou de la simplicité et de la rapidité?
J'ai choisis de privilègié la rapidité en utilisant une table

Mon algorithme travaillera sur des tableau plats d'entiers et construira donc les leg et le Journey qu'une seule fois ce qui occasionne un gain en rapidité
