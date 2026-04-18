# Agent Gmail

Lokalny asystent Gmail do porzadkowania poczty, pobierania faktur i przygotowywania raportow dziennych oraz tygodniowych.

## Co dziala teraz

- panel webowy z podsumowaniem poczty,
- raport dzienny i tygodniowy,
- lista wiadomosci wymagajacych decyzji,
- reguly pobierania zalacznikow od konkretnych nadawcow,
- synchronizacja Gmail zapisujaca pasujace dokumenty do folderow z regul,
- historia pobranych dokumentow z informacja o folderze docelowym,
- status przy wiadomosci: pobrano, brak reguly albo do sprawdzenia,
- lista waznych nadawcow, ktorzy automatycznie podbijaja wiadomosci do uwagi,
- szkic odpowiedzi generowany lokalnie na potrzeby prototypu.

## Uruchomienie

```powershell
python server.py
```

Nastepnie otworz:

```text
http://127.0.0.1:4188
```

Na Windows aplikacje mozesz uruchomic skrotem z pulpitu. Skrot odpala:

```text
start_agent_gmail.ps1
```

Launcher uruchamia lokalny serwer i otwiera panel w osobnym oknie przegladarki w trybie aplikacji.

## Gmail OAuth

Plik OAuth z Google Cloud zapisz lokalnie tutaj:

```text
secrets/gmail_oauth_client.json
```

Folder `secrets/` nie trafia do Git. Aplikacja prosi na start tylko o zakres:

```text
https://www.googleapis.com/auth/gmail.readonly
```

Po kliknieciu przycisku polaczenia Gmail token uzytkownika zostanie zapisany lokalnie w `tokens/`, ktory takze nie trafia do Git.

## Bezpieczenstwo

- AI proponuje odpowiedzi, ale ich samo nie wysyla.
- Zalaczniki pobieramy tylko od zaufanych nadawcow.
- ZIP i podejrzane rozszerzenia wymagaja recznego sprawdzenia.
- Na start aplikacja ma tylko odczyt Gmaila przez `gmail.readonly`.
- Tokeny OAuth trzymamy lokalnie w `tokens/`, poza repo.

## Jak beda dzialac faktury i dokumenty

Regula ma nadawce, slowa kluczowe, etykiete i folder docelowy. Gdy agent znajdzie
pasujaca wiadomosc, pobierze bezpieczne typy plikow, np. PDF, XML, DOCX albo XLSX,
do wskazanego folderu na komputerze. Przy wiadomosci pojawi sie status pobrania,
a w sekcji "Pobrane dokumenty" bedzie widac plik, nadawce, regule i pelna sciezke.

## Wazni nadawcy

Lista waznych nadawcow dziala niezaleznie od regul pobierania plikow. Wiadomosci
od takich adresow dostaja priorytet, etykiete do uwagi i trafiaja wyzej na liscie.
To dobre miejsce na stalych klientow, ksiegowa, urzedy, bank, dostawcow albo osoby,
ktorych wiadomosci nie powinny zginac w codziennym szumie.
