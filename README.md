# Agent Gmail

Lokalny prototyp asystenta Gmail do porzadkowania poczty, pobierania faktur i przygotowywania raportow dziennych oraz tygodniowych.

## Co dziala teraz

- panel webowy z podsumowaniem poczty,
- raport dzienny i tygodniowy na danych demo,
- lista wiadomosci wymagajacych decyzji,
- reguly pobierania zalacznikow od konkretnych nadawcow,
- synchronizacja demo tworzaca testowe pliki w `downloads/`,
- szkic odpowiedzi generowany lokalnie na potrzeby prototypu.

## Uruchomienie

```powershell
python server.py
```

Nastepnie otworz:

```text
http://127.0.0.1:4188
```

## Nastepny etap

1. Utworzyc projekt w Google Cloud.
2. Wlaczyc Gmail API.
3. Skonfigurowac OAuth Consent Screen.
4. Dodac OAuth Client ID dla aplikacji webowej.
5. Uzupelnic `.env`.
6. Zastapic demo endpoint `/api/sync` prawdziwym pobieraniem maili i zalacznikow.
7. Dodac OpenAI API do klasyfikacji, streszczen i szkicow odpowiedzi.

## Bezpieczenstwo

- AI proponuje odpowiedzi, ale ich samo nie wysyla.
- Zalaczniki pobieramy tylko od zaufanych nadawcow.
- ZIP i podejrzane rozszerzenia wymagaja recznego sprawdzenia.
- Tokeny OAuth trzymamy lokalnie w `tokens/`, poza repo.
