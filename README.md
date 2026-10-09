# Aquarium Manager

Aquarium Manager is a custom Home Assistant integration for managing a planted freshwater aquarium.

## Features

- Aquarium dimensions and calculated water volume
- Water test entry for NH3/NH4, NO2, NO3, pH, GH and KH
- Persistent livestock stocking and species profiles
- Relative bioload and stocking assessment
- Compatible-species suggestions and group-size warnings
- GitHub-backed fish species library with in-Home-Assistant species import
- Backup heater fail-safe automation
- Two-light, three-mode lighting scheduler
- Coordinated injected CO2 scheduling
- Mobile-friendly tabbed Lovelace card

## HACS installation

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/Pogo86/aquarium-manager`.
3. Select **Integration** as the category.
4. Download **Aquarium Manager**.
5. Restart Home Assistant.
6. If Aquarium Manager is not already configured, add it from **Settings → Devices & services → Add integration**.

Existing manual Aquarium Manager config entries and stored aquarium data are retained because HACS replaces the integration code only.

## Dashboard card

The Lovelace card is bundled with the integration and served from a stable URL. Add this resource once under **Settings → Dashboards → Resources**:

```text
/aquarium_manager/aquarium-manager-card.js
```

Resource type: **JavaScript Module**.

Then add the card:

```yaml
type: custom:aquarium-manager-card
tank_name: Main Aquarium
entity_prefix: main_aquarium
```

Do not add a version query string to the resource URL. The integration serves the bundled card with caching disabled so the same URL can be used after future HACS upgrades.

### Migrating from the old manual card

Replace the old resource such as:

```text
/local/aquarium-manager-card.js?v=0.8.4
```

with:

```text
/aquarium_manager/aquarium-manager-card.js
```

After confirming the new card loads, the old `/config/www/aquarium-manager-card.js` file can be deleted.

## Releases

For clean HACS update tracking, publish GitHub releases using tags that match `manifest.json`, for example `v0.9.2`.

## Fish library

The species database can remain in a separate repository. Aquarium Manager accepts the configured raw GitHub JSON URL and can write changes back when a suitable fine-grained GitHub token is configured.

## License

MIT


## v0.9.2

Backup heater control is now a two-option **Auto / Off** mode selector. **Off** immediately forces the configured heater plug off and keeps the fail-safe controller disabled. **Auto** returns control to the configured temperature thresholds, delay and maximum runtime.
