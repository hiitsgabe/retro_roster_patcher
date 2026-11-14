"""Spanish, French and Portuguese for the community English patch's full-width
text, keyed by the English exactly as the patched disc holds it.

Each entry is (es, fr, pt). Accents are written as they should read and
dropped on encoding: the game's font has none. A translation must fit the
bytes of the English plus the spaces after it (two bytes a letter, one a
space); `tests/games/we2002/test_fullwidth.py` checks every entry.

Fragments of strings, Japanese leftovers and position codes (WG, CF, GK...)
are left out on purpose.
"""

from __future__ import annotations

import re

_W = "　"  # full-width space: the tables below align their columns with it
_ROWS: dict[str, tuple[str, str, str]] = {
    # --- in-match pause menu, training, camera and sound ---
    "Exit": ("Salir|Fin", "Quitter|Fin", "Sair"),
    "Repeat": ("Repetir", "Répéter", "Repetir"),
    f"Are you sure?\n\x0b{_W * 4}Yes\n\x0b{_W * 4}No": (
        f"Seguro?\n\x0b{_W * 4}Sí\n\x0b{_W * 4}No",
        f"Sûr ?\n\x0b{_W * 4}Oui\n\x0b{_W * 4}Non",
        f"Certeza?\n\x0b{_W * 4}Sim\n\x0b{_W * 4}Não",
    ),
    "Injured Player": ("Lesionado", "Blessé", "Lesionado"),
    "Auto/Manual ■ Change": (
        "Auto/Manual ■ Cambiar",
        "Auto/Manuel ■ Changer",
        "Auto/Manual ■ Trocar",
    ),
    "Strategy": ("Estrategia", "Stratégie", "Estratégia"),
    "Score": ("Marcador", "Score", "Placar"),
    "Time": ("Tiempo", "Temps", "Tempo"),
    "Player Name": ("Nombre", "Nom joueur", "Nome"),
    "Radar": ("Radar", "Radar", "Radar"),
    "Load/Save Strategy": ("Cargar/Grabar estr.", "Charger/Sauver strat.", "Carregar/Salvar estr."),
    "Elabor. Strategy": ("Crear estrategia", "Créer stratégie", "Criar estratégia"),
    "Speed slider": ("Velocidad", "Vitesse", "Velocidade"),
    "Speed set -/+": ("Velocidad −/+", "Vitesse −/+", "Velocidade −/+"),
    "Right Corner Kicks": ("Córners derecha", "Corners droite", "Escanteios direita"),
    "Left Corner Kicks": ("Córners izquierda", "Corners gauche", "Escanteios esquerda"),
    "Free Kick Practice": ("Tiros libres", "Coups francs", "Treino de faltas"),
    "1-2 Practice": ("Paredes 1−2", "Une-deux", "Tabelas 1−2"),
    "Attack Practice": ("Práctica de ataque", "Exercice d'attaque", "Treino de ataque"),
    "BGM": ("Música", "Musique", "Música"),
    "Sound Effect": ("Efectos", "Effets", "Efeitos"),
    "Commentary": ("Comentarios", "Commentaires", "Narração"),
    "Audio": ("Audio", "Audio", "Áudio"),
    "Overhead Far": ("Aérea lejos", "Aérien loin", "Aérea longe"),
    "Overhead Mid.": ("Aérea media", "Aérien moyen", "Aérea média"),
    "Overhead Near": ("Aérea cerca", "Aérien près", "Aérea perto"),
    "Zoom": ("Zoom", "Zoom", "Zoom"),
    "TV": ("TV", "TV", "TV"),
    "Wide": ("Amplia", "Large", "Ampla"),
    "Normal Far": ("Normal lejos", "Normal loin", "Normal longe"),
    "Normal Mid.": ("Normal media", "Normal moyen", "Normal média"),
    "Normal Near": ("Normal cerca", "Normal près", "Normal perto"),
    "Exit Training": ("Salir", "Quitter", "Sair"),
    "Training set": ("Ajustes", "Réglages", "Ajustes"),
    "Continue": ("Continuar", "Continuer", "Continuar"),
    "Exit from Match": ("Salir del partido", "Quitter le match", "Sair da partida"),
    "Sound set": ("Sonido", "Son", "Som"),
    "Display Switches": ("Pantalla", "Affichage", "Exibição"),
    "Camera": ("Cámara", "Caméra", "Câmera"),
    "Formation set": ("Formación", "Formation", "Formação"),
    "Start!": ("Empezar!", "Go !", "Começar!"),
    # --- memory card ---
    "Memory Card is used\nto load Options": (
        "Memory Card para\ncargar opciones",
        "Carte mémoire pour\ncharger options",
        "Memory Card para\ncarregar opções",
    ),
    "Memory Card is used\nto save Options": (
        "Memory Card para\ngrabar opciones",
        "Carte mémoire pour\nsauver options",
        "Memory Card para\nsalvar opções",
    ),
    "Select the Memory Card": (
        "Elige la Memory Card",
        "Choisir carte mémoire",
        "Escolha o Memory Card",
    ),
    "Loading Data in\nthe Memory Card": (
        "Cargando datos de\nla Memory Card",
        "Chargement des\ndonnées",
        "Carregando dados\ndo Memory Card",
    ),
    "Complete": ("Completo", "Terminé", "Completo"),
    "Load": ("Cargar|Carga", "Charger|Lire", "Carregar|Ler"),
    "Done": ("Hecho", "Fait", "Feito"),
    "Creat. File": ("Crear arch.", "Créer fich.", "Criar arq."),
    "Save": ("Grabar|Graba", "Sauver|Sauv.", "Salvar|Salva"),
    "ERROR": ("ERROR", "ERREUR", "ERRO"),
    "Canc.": ("Canc.", "Ann.", "Canc."),
    "Defaults": ("Original", "Défaut", "Padrão"),
    "No Options": ("Sin opciones", "Aucune option", "Sem opções"),
    "Full": ("Lleno", "Plein", "Cheio"),
    "Checking...": ("Revisando...", "Contrôle...", "Checando..."),
    "Select the Data": ("Elige los datos", "Choisir données", "Escolha os dados"),
    "Formation used": ("Formación usada", "Form. utilisée", "Formação usada"),
    "Master League": ("Liga Master", "Ligue Master", "Liga Master"),
    "League        Internat.": (
        "Liga          Internac.",
        "Ligue         Internat.",
        "Liga          Internac.",
    ),
    "Options": ("Opciones", "Options", "Opções"),
    "Already used": ("Ya usado", "Déjà utilisé", "Já usado"),
    "Empty": ("Vacío", "Vide", "Vazio"),
    "No Data stored": ("Sin datos", "Aucune donnée", "Sem dados"),
    "Error\n● Repeat ★ Cancel": (
        "Error\n● Repetir ★ Cancelar",
        "Erreur\n● Répéter ★ Annuler",
        "Erro\n● Repetir ★ Cancelar",
    ),
    "Error with the Memory Card\n● Repeat ★ Cancel": (
        "Error en la Memory Card\n● Repetir ★ Cancelar",
        "Erreur carte mémoire\n● Répéter ★ Annuler",
        "Erro no Memory Card\n● Repetir ★ Cancelar",
    ),
    "Memory Card is full \n★ Repeat": (
        "Memory Card llena\n★ Repetir",
        "Carte pleine\n★ Répéter",
        "Memory Card cheio\n★ Repetir",
    ),
    "Aborted": ("Cancelado", "Annulé", "Cancelado"),
    "Delete Block\nSTART Confirm ★ Cancel": (
        "Borrar bloque\nSTART Sí ★ Cancelar",
        "Effacer bloc\nSTART Oui ★ Annuler",
        "Apagar bloco\nSTART Sim ★ Cancelar",
    ),
    "Confirm Changes?\nSTART Yes ★ NO": (
        "Confirmar cambios?\nSTART Sí ★ No",
        "Confirmer ?\nSTART Oui ★ Non",
        "Confirmar mudanças?\nSTART Sim ★ Não",
    ),
    "Save?\nSTART Yes ★ No": (
        "Grabar?\nSTART Sí ★ No",
        "Sauver ?\nSTART Oui ★ Non",
        "Salvar?\nSTART Sim ★ Não",
    ),
    "Saving...": ("Grabando...", "Sauvegarde...", "Salvando..."),
    "Press START to save": ("START para grabar", "START pour sauver", "START para salvar"),
    "Loading...": ("Cargando...", "Chargement...", "Carregando..."),
    "Press START to load": ("START para cargar", "START pour charger", "START para carregar"),
    "Modify?\n● Yes ★ No": (
        "Modificar?\n● Sí ★ No",
        "Modifier ?\n● Oui ★ Non",
        "Modificar?\n● Sim ★ Não",
    ),
    "Select a Place": ("Elige un lugar", "Choisir un emplacement", "Escolha um local"),
    "Select...": ("Elige...", "Choisir...", "Escolha..."),
    "No Data\nof WINNING ELEVEN 2002": (
        "Sin datos de\nWINNING ELEVEN 2002",
        "Aucune donnée\nWINNING ELEVEN 2002",
        "Sem dados do\nWINNING ELEVEN 2002",
    ),
    "No Memory Card": ("Sin Memory Card", "Pas de carte", "Sem Memory Card"),
    "Select Mem. Card": ("Elige Memory Card", "Choisir carte", "Escolha Mem. Card"),
    "Succesfully completed": ("Completado", "Terminé", "Concluído"),
    "Operation succesfully\ncompleted": (
        "Operación\ncompletada",
        "Opération\nterminée",
        "Operação\nconcluída",
    ),
    "Ready": ("Listo", "Prêt", "Pronto"),
    "Error with the Memory Card": (
        "Error en la Memory Card",
        "Erreur carte mémoire",
        "Erro no Memory Card",
    ),
    "No Option": ("Sin opciones", "Aucune option", "Sem opções"),
    "Saving Data...": ("Grabando datos...", "Sauvegarde...", "Salvando dados..."),
    "START format\n● Try\n★ Continue": (
        "START formatear\n● Probar\n★ Continuar",
        "START formater\n● Encore\n★ Continuer",
        "START formatar\n● Tentar\n★ Continuar",
    ),
    "No Data": ("Sin datos", "Aucune donnée", "Sem dados"),
    "Can’t save": ("No se graba", "Pas de sauv.", "Não salva"),
    "No Data found": ("No hay datos", "Aucune donnée", "Nenhum dado"),
    "START to create\n● Search\n★ Cancel": (
        "START crear\n● Buscar\n★ Cancelar",
        "START créer\n● Chercher\n★ Annuler",
        "START criar\n● Procurar\n★ Cancelar",
    ),
    "Must have 2 free\nBlocks\n● Repeat\n★ Cancel": (
        "Se necesitan 2\nbloques libres\n● Repetir\n★ Cancelar",
        "Il faut 2 blocs\nlibres\n● Répéter\n★ Annuler",
        "Precisa de 2\nblocos livres\n● Repetir\n★ Cancelar",
    ),
    "Loading...\nDon’t remove the Memory Card": (
        "Cargando...\nNo saques la Memory Card",
        "Chargement...\nNe retirez pas la carte",
        "Carregando...\nNão tire o Memory Card",
    ),
    "Searching...\nDon’t remove the Mem. Card": (
        "Buscando...\nNo saques la Memory Card",
        "Recherche...\nNe retirez pas la carte",
        "Procurando...\nNão tire o Memory Card",
    ),
    # --- options and the mode menus ---
    "Use Joypad to move\n● Accept ★ Cancel": (
        "Mueve con el mando\n● Aceptar ★ Cancelar",
        "Déplacer à la manette\n● OK ★ Annuler",
        "Mova com o controle\n● Aceitar ★ Cancelar",
    ),
    "Save Configuration": ("Grabar ajustes", "Sauver réglages", "Salvar ajustes"),
    "Load Configuration": ("Cargar ajustes", "Charger réglages", "Carregar ajustes"),
    "Autosave\nOn/Off": ("Autograbado\nSí/No", "Sauv. auto\nOui/Non", "Salvar auto\nSim/Não"),
    "Modify stored": ("Modificar", "Modifier", "Modificar"),
    "Level\nN/H/E": ("Nivel\nN/D/F", "Niveau\nN/D/F", "Nível\nN/D/F"),
    "Season\nFull/Half": (
        "Temporada\nEntera/Media",
        "Saison\nEntière/Demi",
        "Temporada\nInteira/Meia",
    ),
    "Groups\nNumbers/Letters": (
        "Grupos\nNúmeros/Letras",
        "Groupes\nChiffres/Lettres",
        "Grupos\nNúmeros/Letras",
    ),
    f"{_W}  ▲ Reset\n{_W}  ★ Cancel\n{_W}  ● OK": (
        f"{_W}  ▲ Reinic.\n{_W}  ★ Cancelar\n{_W}  ● OK",
        f"{_W}  ▲ Réinit.\n{_W}  ★ Annuler\n{_W}  ● OK",
        f"{_W}  ▲ Reinic.\n{_W}  ★ Cancelar\n{_W}  ● OK",
    ),
    "Select number of Players": ("Número de jugadores", "Nombre de joueurs", "Número de jogadores"),
    "Home and Away\nNo/Yes": (
        "Ida y vuelta\nNo/Sí",
        "Aller-retour\nNon/Oui",
        "Ida e volta\nNão/Sim",
    ),
    "Number of Teams": ("Número de equipos", "Nombre d'équipes", "Número de times"),
    "Kind of Cup\nCup/League/Group League": (
        "Tipo de copa\nCopa/Liga/Grupos",
        "Type de coupe\nCoupe/Ligue/Groupes",
        "Tipo de copa\nCopa/Liga/Grupos",
    ),
    "Konami Cup\nFree choice": (
        "Copa Konami\nLibre",
        "Coupe Konami\nChoix libre",
        "Copa Konami\nEscolha livre",
    ),
    "Asia Cup\nAsia NT": (
        "Copa de Asia\nSel. de Asia",
        "Coupe d'Asie\nSél. d'Asie",
        "Copa da Ásia\nSel. da Ásia",
    ),
    "America Cup\nAmerica NT": (
        "Copa América\nSel. de América",
        "Coupe Amérique\nSél. d'Amérique",
        "Copa América\nSel. da América",
    ),
    "Africa Cup\nAfrica NT": (
        "Copa de África\nSel. de África",
        "Coupe d'Afrique\nSél. d'Afrique",
        "Copa da África\nSel. da África",
    ),
    "International Cup\nWorld NT": (
        "Copa Internacional\nSel. del mundo",
        "Coupe internat.\nSél. du monde",
        "Copa Internacional\nSel. do mundo",
    ),
    "Select Kits": ("Elige uniformes", "Choisir maillots", "Escolha uniformes"),
    "Select Stadium": ("Elige estadio", "Choisir stade", "Escolha estádio"),
    "Penalty Kicks?\nYes/No": ("Penaltis?\nSí/No", "Tirs au but ?\nOui/Non", "Pênaltis?\nSim/Não"),
    "Golden Goal?\nYes/No": ("Gol de oro?\nSí/No", "But en or ?\nOui/Non", "Gol de ouro?\nSim/Não"),
    "Extra Time?\nYes/No": ("Prórroga?\nSí/No", "Prolongation ?\nOui/Non", "Prorrogação?\nSim/Não"),
    "Match Length\n10/15/20/25/30/5 MIN": (
        "Duración\n10/15/20/25/30/5 MIN",
        "Durée\n10/15/20/25/30/5 MIN",
        "Duração\n10/15/20/25/30/5 MIN",
    ),
    "Weather\nRandom/SS/SR/LS/LR": (
        "Clima\nAzar/SS/SR/LS/LR",
        "Météo\nHasard/SS/SR/LS/LR",
        "Clima\nAleatório/SS/SR/LS/LR",
    ),
    "Time\nRandom/Day/Night": (
        "Hora\nAzar/Día/Noche",
        "Heure\nHasard/Jour/Nuit",
        "Hora\nAleatório/Dia/Noite",
    ),
    "Press ■\nAutomatic/Manual": (
        "Pulsa ■\nAutomático/Manual",
        "Appuyer ■\nAuto/Manuel",
        "Aperte ■\nAutomático/Manual",
    ),
    "Load Master League Team": (
        "Cargar equipo Liga Master",
        "Charger équipe Ligue Master",
        "Carregar time Liga Master",
    ),
    "Play with All Stars": ("Jugar con All Stars", "Jouer avec All Stars", "Jogar com All Stars"),
    "Penalty Kicks": ("Penaltis", "Tirs au but", "Pênaltis"),
    "Friendly Match": ("Amistoso", "Match amical", "Amistoso"),
    "Load Master League": ("Cargar Liga Master", "Charger Ligue Master", "Carregar Liga Master"),
    "New Master League": ("Nueva Liga Master", "Nouvelle Ligue Master", "Nova Liga Master"),
    "Screen set": ("Pantalla", "Écran", "Tela"),
    "Joypad set": ("Mando", "Manette", "Controle"),
    "Replay": ("Repetición", "Ralenti", "Replay"),
    "Memory Card": ("Memory Card", "Carte mémoire", "Memory Card"),
    "Training": ("Entrenamiento", "Entraînement", "Treino"),
    "Load Cup": ("Cargar copa", "Charger coupe", "Carregar copa"),
    "New Cup": ("Nueva copa", "Nouv. coupe", "Nova copa"),
    "Load League": ("Cargar liga", "Charger ligue", "Carregar liga"),
    "New League": ("Nueva liga", "Nouv. ligue", "Nova liga"),
    "Configure the Game": ("Configura el juego", "Régler le jeu", "Configure o jogo"),
    "Make your own Team": ("Crea tu equipo", "Créez votre équipe", "Crie seu time"),
    "Train to improve": ("Entrena para mejorar", "Entraînez-vous", "Treine para melhorar"),
    "Play with the best\nClubs of the World": (
        "Juega con los mejores\nclubes del mundo",
        "Jouez avec les\nmeilleurs clubs",
        "Jogue com os melhores\nclubes do mundo",
    ),
    "Play with the best\nNational Teams of the World": (
        "Juega con las mejores\nselecciones del mundo",
        "Jouez avec les meilleures\nsélections du monde",
        "Jogue com as melhores\nseleções do mundo",
    ),
    "Play an International\nLeague": (
        "Juega una liga\ninternacional",
        "Jouez une ligue\ninternationale",
        "Jogue uma liga\ninternacional",
    ),
    "Play Friendly and\nPenalty Kicks": (
        "Amistosos y\npenaltis",
        "Matchs amicaux et\ntirs au but",
        "Amistosos e\npênaltis",
    ),
    "Restore Defaults": ("Restaurar", "Par défaut", "Restaurar padrão"),
    "● Change ★ Cancel": ("● Cambiar ★ Cancelar", "● Changer ★ Annuler", "● Trocar ★ Cancelar"),
    "● Select Replay": ("● Elige repetición", "● Choisir ralenti", "● Escolha replay"),
    "Select the Joypad\n● Yes ★ Cancel": (
        "Elige el mando\n● Sí ★ Cancelar",
        "Choisir manette\n● Oui ★ Annuler",
        "Escolha controle\n● Sim ★ Cancelar",
    ),
    # --- league and cup menus ---
    "Exit? \n\n\nYes \n\nNo": (
        "Salir? \n\n\nSí \n\nNo",
        "Quitter ?\n\n\nOui\n\nNon",
        "Sair? \n\n\nSim \n\nNão",
    ),
    "\n\nLoad \n\nExit": ("\n\nCargar\n\nSalir", "\n\nCharger\n\nQuitter", "\n\nCarregar\n\nSair"),
    "Play? \n\n\nYes \n\nNo": (
        "Jugar?\n\n\nSí \n\nNo",
        "Jouer ?\n\n\nOui\n\nNon",
        "Jogar?\n\n\nSim \n\nNão",
    ),
    "\n\nPlay \n\nExit": ("\n\nJugar\n\nSalir", "\n\nJouer\n\nQuitter", "\n\nJogar\n\nSair"),
    f"\n{_W}Play\n\n  Save \n\n  Exit": (
        f"\n{_W}Jugar\n\n  Grabar\n\n  Salir",
        f"\n{_W}Jouer\n\n  Sauver\n\n  Quitter",
        f"\n{_W}Jogar\n\n  Salvar\n\n  Sair",
    ),
    "Buy players\nMaximum of 2\nPlayers\nby Game": (
        "Fichajes\nMáximo 2\njugadores\npor partido",
        "Acheter\nMaximum 2\njoueurs\npar match",
        "Contratar\nMáximo de 2\njogadores\npor jogo",
    ),
    "Change\nShirt Number\n": ("Cambiar\ndorsal\n", "Changer\nnuméro\n", "Mudar\nnúmero\n"),
    "Show Team\nGroups": ("Ver los\ngrupos", "Voir les\ngroupes", "Ver os\ngrupos"),
    "Information\nabout\nPlayers": (
        "Datos de\nlos\njugadores",
        "Infos sur\nles\njoueurs",
        "Dados dos\njogadores\n",
    ),
    "Verify\nTable of\nGames": (
        "Ver el\ncalendario\n",
        "Voir le\ncalendrier\n",
        "Ver a\ntabela de\njogos",
    ),
    "Show the 32\nbest Assisters": (
        "Los 32 mejores\nasistentes",
        "Les 32 meilleurs\npasseurs",
        "Os 32 maiores\nassistentes",
    ),
    "Show the 32\nbest Scorers": (
        "Los 32 mejores\ngoleadores",
        "Les 32 meilleurs\nbuteurs",
        "Os 32 maiores\nartilheiros",
    ),
    "Won\nLost\nShow Result\n": (
        "Ganados\nPerdidos\nResultados\n",
        "Gagnés\nPerdus\nRésultats\n",
        "Vitórias\nDerrotas\nResultados\n",
    ),
    "Won/Lost\n\n● Won\n▲ Draw\n★ Lost": (
        "Resultados\n\n● Ganado\n▲ Empate\n★ Perdido",
        "Bilan\n\n● Gagné\n▲ Nul\n★ Perdu",
        "Resultados\n\n● Vitória\n▲ Empate\n★ Derrota",
    ),
    "Results of Team": ("Resultados", "Résultats", "Resultados"),
    "Show\nSchedule\nTable": ("Ver el\ncalendario\n", "Voir le\ncalendrier\n", "Ver a\ntabela\n"),
    "Show Graphic of Team\n\n● Show/Not\nshow\n(Max:6)": (
        "Gráfico del equipo\n\n● Ver/No\nver\n(Máx:6)",
        "Graphique équipe\n\n● Voir/Ne pas\nvoir\n(Max:6)",
        "Gráfico do time\n\n● Ver/Não\nver\n(Máx:6)",
    ),
    "Position of Team\n\nWon:3pts\nDraw:1pt\nLost:0pt": (
        "Posición\n\nGanado:3pts\nEmpate:1pt\nPerdido:0pt",
        "Classement\n\nGagné:3pts\nNul:1pt\nPerdu:0pt",
        "Classificação\n\nVitória:3pts\nEmpate:1pt\nDerrota:0pt",
    ),
    "Games": ("Partidos|Partid.", "Matchs", "Jogos"),
    "Result": ("Result.", "Résult.", "Result."),
    "Graph": ("Gráf.", "Graph.", "Gráf."),
    "W/L": ("G/P", "G/P", "V/D"),
    "Pos": ("Pos", "Cl.", "Pos"),
    "Table": ("Tabla", "Tableau", "Tabela"),
    "Group": ("Grupo", "Groupe", "Grupo"),
    "Pass": ("Pases", "Passes", "Passes"),
    "Scorer": ("Golead.", "Buteurs", "Artilh."),
    "Game": ("Partido|Part.", "Match", "Jogo"),
    "W/L2": ("G/P2", "G/P2", "V/D2"),
    "POS": ("POS", "CL.", "POS"),
    # --- Edit mode ---
    "Choose team...\n■ Show substitute": (
        "Elige equipo...\n■ Ver suplentes",
        "Choisir équipe...\n■ Remplaçants",
        "Escolha o time...\n■ Ver reservas",
    ),
    "Japan - Korea": ("Japón − Corea", "Japon − Corée", "Japão − Coreia"),
    "Nationals": ("Selecciones", "Sélections", "Seleções"),
    "Outside": ("Exterior", "Extérieur", "Trivela"),
    "Response": ("Reacción", "Réaction", "Reação"),
    "Agression": ("Agresividad", "Agressivité", "Agressiv."),
    "Curve": ("Efecto", "Effet", "Curva"),
    "Dribble": ("Regate", "Dribble", "Drible"),
    "Technique": ("Técnica", "Technique", "Técnica"),
    "Head Accuracy": ("Remate cabeza", "Précision tête", "Cabeceio"),
    "Jump Power": ("Salto", "Détente", "Impulsão"),
    "Shot Accuracy": ("Precis. tiro", "Précision tir", "Precis. chute"),
    "Shot Power": ("Potencia tiro", "Puissance tir", "Força chute"),
    "Pass Acc.": ("Prec. pase", "Préc. passe", "Prec. passe"),
    "Aceleration": ("Aceleración", "Accélér.", "Aceleração"),
    "Speed": ("Velocidad", "Vitesse", "Velocidade"),
    "Stamina": ("Resistencia", "Endurance", "Fôlego"),
    "Body Balance": ("Equilibrio", "Équilibre", "Equilíbrio"),
    "Defence": ("Defensa", "Défense", "Defesa"),
    "Offence": ("Ataque", "Attaque", "Ataque"),
    "Undo": ("Deshacer", "Annuler", "Desfazer"),
    "Confirm": ("Confirmar", "Confirmer", "Confirmar"),
    "Foot": ("Pie", "Pied", "Pé"),
    "Boots   ■ Turn": ("Botas   ■ Girar", "Chaussures ■ Tourner", "Chuteira ■ Girar"),
    "Age": ("Edad", "Âge", "Idade"),
    "Body": ("Físico", "Physique", "Físico"),
    "Height": ("Altura", "Taille", "Altura"),
    "Hair Face Colour   ■ Turn": (
        "Color barba   ■ Girar",
        "Couleur barbe   ■ Tourner",
        "Cor da barba   ■ Girar",
    ),
    "Kind of Face Hair   ■ Turn": (
        "Tipo de barba   ■ Girar",
        "Type de barbe   ■ Tourner",
        "Tipo de barba   ■ Girar",
    ),
    "Hair Colour   ■ Turn": (
        "Color pelo   ■ Girar",
        "Couleur cheveux ■ Tourner",
        "Cor do cabelo ■ Girar",
    ),
    "Kind of Hair   ■ Turn": ("Peinado   ■ Girar", "Coiffure   ■ Tourner", "Cabelo   ■ Girar"),
    "Skin Colour   ■ Turn": (
        "Color piel   ■ Girar",
        "Couleur peau   ■ Tourner",
        "Cor da pele   ■ Girar",
    ),
    "Nation": ("País", "Pays", "País"),
    "Select Callname. ■ ▲ Listen\nL1 L2 R1 R2 Page": (
        "Elige el nombre ■ ▲ Oír\nL1 L2 R1 R2 Página",
        "Choisir le nom ■ ▲ Écouter\nL1 L2 R1 R2 Page",
        "Escolha o nome ■ ▲ Ouvir\nL1 L2 R1 R2 Página",
    ),
    "Shirt": ("Dorsal", "Maillot", "Camisa"),
    "Modify": ("Modificar", "Modifier", "Modificar"),
    "New": ("Nuevo", "Nouveau", "Novo"),
    "Skills": ("Habilidades", "Capacités", "Habilidades"),
    "Visual": ("Aspecto", "Apparence", "Visual"),
    "Position": ("Posición", "Poste", "Posição"),
    "Callnames": ("Nombre narrado", "Nom annoncé", "Nome narrado"),
    "Names": ("Nombres", "Noms", "Nomes"),
    "Save Data": ("Grabar datos", "Sauver", "Salvar dados"),
    "Save?\n● Yes ★ No": ("Grabar?\n● Sí ★ No", "Sauver ?\n● Oui ★ Non", "Salvar?\n● Sim ★ Não"),
    "Delete?\n● Yes ★ No": ("Borrar?\n● Sí ★ No", "Effacer ?\n● Oui ★ Non", "Apagar?\n● Sim ★ Não"),
    "Select Player": ("Elige jugador", "Choisir joueur", "Escolha jogador"),
    "Change?\n● Yes ★ No": (
        "Cambiar?\n● Sí ★ No",
        "Changer ?\n● Oui ★ Non",
        "Trocar?\n● Sim ★ Não",
    ),
    "Exit?": ("Salir?", "Quitter ?", "Sair?"),
    "Place to save?": ("Dónde grabar?", "Où sauver ?", "Onde salvar?"),
    "Select Team\nR1+  ■ Show Substitute": (
        "Elige equipo\nR1+  ■ Ver suplentes",
        "Choisir équipe\nR1+  ■ Remplaçants",
        "Escolha o time\nR1+  ■ Ver reservas",
    ),
    "Delete?": ("Borrar?", "Effacer ?", "Apagar?"),
    "Which one to delete?": ("Cuál borrar?", "Lequel effacer ?", "Qual apagar?"),
    "Select Team\n■ Show Substitute": (
        "Elige equipo\n■ Ver suplentes",
        "Choisir équipe\n■ Remplaçants",
        "Escolha o time\n■ Ver reservas",
    ),
    "Choose Number": ("Elige dorsal", "Choisir numéro", "Escolha número"),
    "Edit Names": ("Editar nombres", "Modifier noms", "Editar nomes"),
    "Select Team\n■  Show Substitute": (
        "Elige equipo\n■  Ver suplentes",
        "Choisir équipe\n■  Remplaçants",
        "Escolha o time\n■  Ver reservas",
    ),
    "Save Changes": ("Grabar cambios", "Sauver", "Salvar mudanças"),
    "Edit Numbers": ("Editar dorsales", "Modifier numéros", "Editar números"),
    "Insert Player": ("Añadir jugador", "Ajouter joueur", "Inserir jogador"),
    "Delete Player": ("Borrar jugador", "Effacer joueur", "Apagar jogador"),
    "Edit Player": ("Editar jugador", "Modifier joueur", "Editar jogador"),
    # --- strategies and team files ---
    "Confirm Delete": ("Confirmar borrado", "Confirmer", "Confirmar exclusão"),
    "Select Team to delete": ("Equipo a borrar", "Équipe à effacer", "Time a apagar"),
    "Confirm Copies": ("Confirmar copia", "Confirmer copie", "Confirmar cópia"),
    "Select Team to copy": ("Equipo a copiar", "Équipe à copier", "Time a copiar"),
    "Select Team to load": ("Equipo a cargar", "Équipe à charger", "Time a carregar"),
    "Select Team to save": ("Equipo a grabar", "Équipe à sauver", "Time a salvar"),
    "Delete Strategy \n": ("Borrar estrat.\n", "Effacer strat.\n", "Apagar estrat.\n"),
    "Copy Strategy \n": ("Copiar estrat.\n", "Copier strat.\n", "Copiar estrat.\n"),
    "Change Strategy \n": ("Cambiar estrat.\n", "Changer strat.\n", "Mudar estrat.\n"),
    "Return": ("Volver", "Retour", "Voltar"),
    "Save Strategy. \nSTART Save ★  Cancel": (
        "Grabar estrat.\nSTART Grabar ★ Cancelar",
        "Sauver strat.\nSTART Sauver ★ Annuler",
        "Salvar estrat.\nSTART Salvar ★ Cancelar",
    ),
    "Used - Options": ("Usado − Opciones", "Utilisé−Options", "Usado − Opções"),
    "Used yet": ("Ya usado", "Déjà utilisé", "Já usado"),
    "Empty Space": ("Espacio vacío", "Vide", "Espaço vazio"),
    "Updating, please wait...": (
        "Actualizando, espera...",
        "Mise à jour, patientez...",
        "Atualizando, aguarde...",
    ),
    "Error\n● REPEAT ★ CANCEL": (
        "Error\n● REPETIR ★ CANCELAR",
        "Erreur\n● RÉPÉTER ★ ANNULER",
        "Erro\n● REPETIR ★ CANCELAR",
    ),
    "Deleting\nDon’t move Mem Car": (
        "Borrando\nNo la saques",
        "Effacement\nNe retirez pas",
        "Apagando\nNão tire o cartão",
    ),
    "Delete\nSTART Confirm ★ Cancel": (
        "Borrar\nSTART Sí ★ Cancelar",
        "Effacer\nSTART Oui ★ Annuler",
        "Apagar\nSTART Sim ★ Cancelar",
    ),
    "Save\nSTART Confirm ★ Cancel": (
        "Grabar\nSTART Sí ★ Cancelar",
        "Sauver\nSTART Oui ★ Annuler",
        "Salvar\nSTART Sim ★ Cancelar",
    ),
    "Saving\nDon’t move Mem. Card": (
        "Grabando\nNo la saques",
        "Sauvegarde\nNe retirez pas",
        "Salvando\nNão tire o cartão",
    ),
    "Loading Strategy...": (
        "Cargando estrategia...",
        "Chargement strat...",
        "Carregando estratégia...",
    ),
    "START Confirm\n★    Cancel": (
        "START Confirmar\n★    Cancelar",
        "START Confirmer\n★    Annuler",
        "START Confirmar\n★    Cancelar",
    ),
    "Strategy loaded\n": ("Estrat. cargada\n", "Stratégie chargée\n", "Estrat. carregada\n"),
    "Imposible to do": ("Imposible", "Impossible", "Impossível"),
    "Space enough\n": ("Hay espacio\n", "Place libre\n", "Há espaço\n"),
    "Select ● Load": ("Elige ● Cargar", "Choisir ● Charger", "Escolha ● Carregar"),
    "Select ● Save ■ Delete": (
        "Elige ● Grabar ■ Borrar",
        "Choisir ● Sauver ■ Effacer",
        "Escolha ● Salvar ■ Apagar",
    ),
    "No strategy\n★ Cancel": (
        "Sin estrategia\n★ Cancelar",
        "Aucune stratégie\n★ Annuler",
        "Sem estratégia\n★ Cancelar",
    ),
    "Loading...\nDon’t remove Memory Card": (
        "Cargando...\nNo saques la tarjeta",
        "Chargement...\nLaissez la carte",
        "Carregando...\nNão tire o Memory Card",
    ),
    "No Memory Card\n★ Try again": (
        "Sin tarjeta\n★ Reintentar",
        "Pas de carte\n★ Réessayer",
        "Sem Memory Card\n★ Tentar",
    ),
    "Select Mem Card\n● Yes ★ Cancel": (
        "Elige tarjeta\n● Sí ★ Cancelar",
        "Choisir carte\n● Oui ★ Annuler",
        "Qual cartão?\n● Sim ★ Cancelar",
    ),
}

#: The league statistics menus lay out two columns with full-width spaces.
#: Each word is translated and put back at its English column.
_TABLE_WORDS: dict[str, tuple[str, str, str]] = {
    "Position": ("Posición", "Classement", "Posição"),
    "Buy": ("Fichar", "Acheter", "Contratar"),
    "Result": ("Resultado", "Résultat", "Resultado"),
    "Scorer": ("Goleador", "Buteur", "Artilheiro"),
    "W/L": ("G/P", "G/P", "V/D"),
    "Pass": ("Pases", "Passes", "Passes"),
    "W/L2": ("G/P2", "G/P2", "V/D2"),
    "2 Div": ("2 Div", "2 Div", "2 Div"),
    "1 Div": ("1 Div", "1 Div", "1 Div"),
    "Graph": ("Gráfico", "Graphique", "Gráfico"),
    "Table": ("Tabla", "Tableau", "Tabela"),
    "Players": ("Jugadores", "Joueurs", "Jogadores"),
    "Games": ("Partidos|Partid.", "Matchs", "Jogos"),
    "Yes": ("Sí", "Oui", "Sim"),
    "No.": ("Núm.", "Num.", "Núm."),
}

_TABLES = [
    f"Position{_W * 5}Buy\n\nResult{_W * 6}Scorer\n\nW/L{_W * 8}Pass\n\n"
    f"W/L2{_W * 7}2 Div\n\n"
    f"Graph{_W * 7}Table{_W}\n\nPlayers{_W * 6}Games{_W}\n\n{_W * 10}Yes",
    f"Buy{_W * 9}No.\n\n{_W * 12}Yes",
    f"Position{_W * 5}Graph\n\nResult{_W * 7}Table{_W * 2}\n\n"
    f"W/L{_W * 8}Games{_W * 2}\n\nW/L2{_W * 8}Yes",
    f"Position{_W * 5}Graph\n\nResult{_W * 6}Table{_W * 2}\n\n"
    f"W/L{_W * 8}Games{_W * 2}\n\nW/L2{_W * 8}Yes",
    f"Position{_W * 4}Buy\n\nResult{_W * 6}Scorer\n\nW/L{_W * 9}Pass\n\n"
    f"W/L2{_W * 8}1 Div\n\n"
    f"Graph{_W * 7}Table\n\nPlayers{_W * 5}Games{_W * 2}\n\n{_W * 12}Yes",
    f"Position{_W * 4}Scorer\n\nW/L{_W * 9}Pass\n\nPlayers{_W * 5}Table\n\n{_W * 12}Yes",
    f"Table{_W * 7}Pass \n\nPlayers{_W * 5}Games{_W}\n\nScorer{_W * 6}Yes",
    f"Position{_W * 4}Players\n\nResult{_W * 6}Scorer\n\nW/L{_W * 9}Pass\n\n"
    f"W/L2{_W * 8}Table{_W * 2}\n\n"
    f"Graph{_W * 7}Games{_W * 2}\n\n{_W * 12}Yes",
]


def _retable(english: str, lang: int) -> str:
    """`english` with every word translated, each kept at its English column
    where the translation leaves room, else one space after the word before."""
    lines = []
    for line in english.split("\n"):
        out = ""
        for match in re.finditer(rf"[^{_W}]+", line):
            word = match.group(0)
            core = word.rstrip(" ")
            tail = word[len(core) :]
            new = _TABLE_WORDS[core][lang] if core in _TABLE_WORDS else core
            gap = max(match.start() - len(out), 1 if out else 0)
            out += _W * gap + new + tail
        width = len(line)
        lines.append(out + _W * max(0, width - len(out)))
    return "\n".join(lines)


FULLWIDTH: dict[str, dict[str, str]] = {
    english: dict(zip(("es", "fr", "pt"), row, strict=True)) for english, row in _ROWS.items()
}
for _table in _TABLES:
    FULLWIDTH[_table] = {code: _retable(_table, i) for i, code in enumerate(("es", "fr", "pt"))}

#: Plain ASCII strings the community patch writes whole or in pieces that the
#: record-level dictionary in `menu_records` never matches. Same (es, fr, pt)
#: layout; a translation must fit the English string's bytes, spaces included.
_ASCII_ROWS: dict[str, tuple[str, str, str]] = {
    "JOYPAD 1-C": ("MANDO 1-C", "MANETTE 1-C", "CONTR. 1-C"),
    "JOYPAD 1-B": ("MANDO 1-B", "MANETTE 1-B", "CONTR. 1-B"),
    "JOYPAD 1-A": ("MANDO 1-A", "MANETTE 1-A", "CONTR. 1-A"),
    " JOYPAD  2": (" MANDO   2", "MANETTE  2", " CONTR.  2"),
    "JOYPAD 2": ("MANDO 2", "MANETTE 2", "CONTR. 2"),
    "JOYPAD 1": ("MANDO 1", "MANETTE 1", "CONTR. 1"),
    "1-2 SHOOT PRAC.": ("PAREDES 1-2", "UNE-DEUX", "TABELAS 1-2"),
    "USE        JOYPAD 1-A": (
        "USA        MANDO 1-A",
        "UTIL.     MANETTE 1-A",
        "USE        CONTR. 1-A",
    ),
    "NORMAL FAR": ("NORMAL LEJ.", "NORMAL LOIN", "NORMAL LON."),
    "NORMAL MID": ("NORMAL MED.", "NORMAL MOY.", "NORMAL MED."),
    "NORMAL NEAR": ("NORMAL CER.", "NORMAL PRES", "NORMAL PER."),
    "PAUSE JOYPAD   1-A": ("PAUSA MANDO    1-A", "PAUSE MANETTE  1-A", "PAUSA CONTR.   1-A"),
    "MEM CAR": ("TARJETA", "CARTE", "CARTAO"),
    "BGM": ("MUSICA", "MUSIQUE", "MUSICA"),
    "SOU": ("EFE", "SON", "SOM"),
    "COM": ("COM", "COM", "NAR"),
    "   LEFT": ("   IZQ.", " GAUCHE", "   ESQ."),
    "  RIGHT": ("   DER.", " DROITE", "   DIR."),
    "POSITION": ("POSICION", "POSTE", "POSICAO"),
    "USING....  SAVING": ("USANDO...  GRABANDO", "UTILISE... SAUVEGARDE", "USANDO...  SALVANDO"),
    "DEFENSE TYPE": ("TIPO DEFENSA", "TYPE DEFENSE", "TIPO DEFESA"),
    "CHANGEABLE": ("CAMBIABLE", "MODIFIABLE", "ALTERAVEL"),
    "UNUSED": ("SIN USAR", "INUTILISE", "SEM USO"),
    "CPU STRAT.": ("ESTR. CPU", "STRAT. CPU", "ESTR. CPU"),
    "$DIVISION 2": ("$DIVISION 2", "$DIVISION 2", "$DIVISAO 2"),
    "USE JOYPAD 1-C": ("USA MANDO 1-C", "MANETTE 1-C", "USE CONTR. 1-C"),
    "USE JOYPAD 1-B": ("USA MANDO 1-B", "MANETTE 1-B", "USE CONTR. 1-B"),
    "USE JOYPAD 1-A": ("USA MANDO 1-A", "MANETTE 1-A", "USE CONTR. 1-A"),
    "USE JOYPAD 2": ("USA MANDO 2", "MANETTE 2", "USE CONTR. 2"),
    "USE JOYPAD 1": ("USA MANDO 1", "MANETTE 1", "USE CONTR. 1"),
    "2nd STAGE": ("2a FASE", "2e PHASE", "2a FASE"),
    "1st STAGE": ("1a FASE", "1re PHASE", "1a FASE"),
    " 5 MIN  SITUAT.": (" 5 MIN  SITUAC.", " 5 MIN  SITUAT.", " 5 MIN  SITUAC."),
    "MEM CARD SLOT": ("SLOT TARJETA", "EMPL. CARTE", "SLOT CARTAO"),
    # nations and classic teams the patcher does not rename
    "Kazakhs": ("Kazajis", "Kazakhs", "Cazaque"),
    "Thailan": ("Tailand", "Thailan", "Tailand"),
    "New Zeland": ("N.Zelanda", "N.Zelande", "N.Zelandia"),
    "Ivory Coast": ("C.Marfil", "C.Ivoire", "C.Marfim"),
    "Nor.Ireland": ("Irl.Norte", "Irl.Nord", "Irl.Norte"),
    "Luxembu": ("Luxembu", "Luxembo", "Luxembu"),
    "Macedon": ("Macedon", "Macedoi", "Macedon"),
    "Bieloru": ("Bielorr", "Bielor.", "Bielorr"),
    "Uzbekistan": ("Uzbekistan", "Ouzbekist", "Uzbequist."),
    "Saudi Arabi": ("A.Saudi", "A.Saoudite", "A.Saudita"),
    "S.Afric": ("Sudafri", "Afr.Sud", "Afr.Sul"),
    "Cameroo": ("Camerun", "Camerou", "Camaroe"),
    "Yugoslavia": ("Yugoslavia", "Yougoslav.", "Iugoslavia"),
    "Sloveni": ("Esloven", "Sloveni", "Esloven"),
    "Slovaki": ("Eslovaq", "Slovaqu", "Eslovaq"),
    "Netherl": ("Holanda", "Pays-Ba", "Holanda"),
    "Class.Argentina": ("Clas.Argentina", "Clas.Argentine", "Clas.Argentina"),
    "Class.Italy": ("Clas.Italia", "Clas.Italie", "Clas.Italia"),
    "Clas.Nether": ("Clas.Holand", "Clas.P-Bas", "Clas.Holand"),
    "Clas.France": ("Clas.Franci", "Clas.France", "Clas.Franca"),
    "SAUDI ARABI": ("A.SAUDI", "A.SAOUDITE", "A.SAUDITA"),
    "YUGOSLAVIA": ("YUGOSLAVIA", "YOUGOSLAV.", "IUGOSLAVIA"),
    "ARGEN.CLASS": ("CLAS.ARGEN.", "CLAS.ARGEN.", "CLAS.ARGEN."),
    "BRAZIL CLAS": ("CLAS.BRASIL", "CLAS.BRESIL", "CLAS.BRASIL"),
    "DEUTCH.CLAS": ("CLAS.ALEMAN", "CLAS.ALLEM.", "CLAS.ALEMAN"),
    "ENGLAND CLA": ("CLAS.INGLAT", "CLAS.ANGLET", "CLAS.INGLAT"),
}

ASCII: dict[str, dict[str, str]] = {
    english: dict(zip(("es", "fr", "pt"), row, strict=True)) for english, row in _ASCII_ROWS.items()
}
