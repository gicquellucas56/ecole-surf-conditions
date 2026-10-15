# Conditions de surf – École-Surf.fr

Robot qui met à jour toutes les heures les conditions (houle, eau, marée, vent, météo)
des spots affichés sur les pages régions d'ecole-surf.fr.

- `spots.json` : liste des spots par région (ajouter/retirer un spot ici).
- `build.py` : récupère les données et écrit `public/conditions.json`.
- `.github/workflows/update.yml` : lancement automatique toutes les heures + publication sur GitHub Pages.

## Sources (gratuites, usage commercial autorisé avec attribution)
- Houle, température de l'eau, marée (façade Atlantique/Manche) : Copernicus Marine Service.
- Vent, air, ciel : MET Norway – api.met.no (CC BY 4.0).

## Secrets à renseigner (Settings → Secrets and variables → Actions)
- `COPERNICUS_USERNAME` : identifiant du compte Copernicus Marine
- `COPERNICUS_PASSWORD` : mot de passe du compte Copernicus Marine
