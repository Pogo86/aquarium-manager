# Aquarium Manager

## v0.9.3 highlights

- Desktop tabs use a two-column layout while the existing mobile layout remains unchanged.
- Water readings are updated from a popup instead of a second inline set of input boxes.
- NH₃ and NO₃ have 10-minute reagent timers; NO₂ has a 2-minute timer.
- Timer completion can send to a configured Home Assistant `notify` entity, with a persistent-notification fallback.


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

For clean HACS update tracking, publish GitHub releases using tags that match `manifest.json`, for example `v0.9.3`.

## Fish library

The species database can remain in a separate repository. Aquarium Manager accepts the configured raw GitHub JSON URL and can write changes back when a suitable fine-grained GitHub token is configured.

## License

MIT


## v0.9.3

Backup heater control is now a two-option **Auto / Off** mode selector. **Off** immediately forces the configured heater plug off and keeps the fail-safe controller disabled. **Auto** returns control to the configured temperature thresholds, delay and maximum runtime.

## v0.9.4 filter management

- Adds a built-in Fluval 307 profile with rated circulation, tank range and media volumes.
- Adds a Filter section to the Equipment tab with calculated turnover, media stages and maintenance status.
- Supports optional measured flow; measured flow is preferred over the manufacturer's rated circulation for turnover calculations.
- Adds editable vertical pre-filter, bottom, middle and top basket media.
- Adds filter maintenance tracking and a **Mark filter cleaned** action.
- Feeds filtration condition into the stocking assessment without treating filter capacity as permission to overstock.

## v0.9.6 attention navigation

- Makes the top-right aquarium status chip hoverable on desktop.
- Shows current water, stocking, filtration, heater and lighting attention items in a compact popover.
- Clicking an attention item jumps to the relevant Aquarium Manager tab and section.
- Clicking the main status chip jumps to the highest-priority attention item.
- Mobile keeps simple tap-to-navigate behavior without relying on hover.

## v0.9.9 dashboard tank switching

Aquarium Manager can discover all configured Aquarium Manager tanks automatically and switch between them from one dashboard card.

```yaml
type: custom:aquarium-manager-card
tank_name: Main Aquarium
entity_prefix: main_aquarium
```

The existing single-tank YAML is enough. After Home Assistant loads the card, it requests the configured aquarium list from the integration and uses the actual Home Assistant entity IDs for each tank. A tank selector appears automatically when more than one Aquarium Manager config entry exists.

Manual `tanks:` configuration remains supported as an override, but is no longer required.

## v0.9.8 shrimp and multi-aquarium support

Aquarium Manager now supports mixed multi-tank setups including dedicated freshwater shrimp aquariums.

- Add each physical aquarium as its own Aquarium Manager config entry.
- Choose **Shrimp aquarium** during setup for a dedicated shrimp tank.
- Livestock records can be Fish, Shrimp, Snail or Other.
- Shrimp and other invertebrates are excluded from fish-only shoaling and relative fish-bioload calculations.
- Shrimp/invertebrate records can store temperature, pH, GH, KH and TDS ranges.
- TDS and source-water TDS entities are available; the shrimp dashboard adds TDS to the water-test view.
- Fish-only compatible-fish and species-library navigation is hidden when a card tank is marked with `tank_profile: shrimp`.

Example dashboard:

```yaml
type: custom:aquarium-manager-card
tanks:
  - tank_name: Main Aquarium
    entity_prefix: main_aquarium
    water_type: Freshwater
    tank_profile: community
  - tank_name: Shrimp Tank
    entity_prefix: shrimp_tank
    water_type: Freshwater
    tank_profile: shrimp
```

Existing single-tank and fish records remain compatible; missing livestock types are treated as Fish.

