# Satellite images on the main map

The SAR workbench opens in a separate tab (`target="_blank"`). The main map's **Satellite images** button opens a movable, non-modal window. Drag its header or use the arrow keys while the header has focus. Closing the window keeps the selected overlays visible; **Reset overview** hides them without discarding the loaded images.

## Open a completed image

1. Open the SAR workbench from the main map. Unlock the workspace and select a completed real image analysis.
2. Choose **Show image on main map**, then return to the original map tab. Only the selected preview and its scene/time/processed-area metadata are transferred. Passwords, GitHub tokens, candidates and analyst notes are not transferred.
3. If the original map tab was closed, choose **Open a new map tab and send image**. Allow that user-initiated tab to open. If browser tab messaging is unavailable, save the encrypted session and use the local-file option below.
4. Alternatively, open **Satellite images → Open encrypted results** on the main map, select the existing encrypted result ZIP or session JSON and enter the workspace password. Decryption happens locally; the file is not uploaded.

Use **Show processed image footprint** and **Show SAR image preview** independently. A transferred preview initially shows only its footprint; local imports initially show neither overlay. Choose an image in the selector to control its visibility and opacity. **Fit image area** moves the map only when requested.

**Download PNG** saves the existing derived Sentinel-1 VV quicklook. **Download footprint** saves a GeoJSON polygon with the acquisition time, scene identifier and preview dimensions. This polygon describes the processed request area, not the entire satellite acquisition footprint. The quicklook is limited to 1,200 pixels per side and 2 MiB. The processing pipeline deletes the temporary raw GeoTIFF; this feature cannot download it or reconstruct a full-resolution scene.

## Local handling and analytical limits

- At most six previews are held in a map tab. No image or decrypted workspace is written to repository files, browser storage or a server. Downloading is an explicit local action.
- **Remove image** removes one image. **Clear images / lock**, reloading or closing the tab removes the loaded previews. Locking the separate workbench cancels pending transfers but does not remove an image already sent to the main map; clear that tab separately.
- Existing encrypted artifact expiry, acquisition limits, workflows and public data contracts remain unchanged. Opening this window does not collect imagery or start Actions.
- Image acquisition time is separate from vessel observation time. Overlay proximity is not evidence that a vessel was present during acquisition. No candidate/AIS association, detection gate or score is changed.
- Invalid, oversized, unsupported or incorrectly decrypted inputs produce an explicit error while preserving previously loaded images. Historical valid images remain visibly timestamped; no current-image claim is made.

## Local validation

Node 24 unit tests cover bounded image/georeference validation, transfer allowlisting, actual encrypted workspace decryption, session-scoped tab messaging, cancellation and the new-tab fallback. The map DOM tests use jsdom and real Leaflet to exercise independent overlays, opacity, downloads, clearing, the six-image cap, map reset and existing vessel search without provider calls. Browser rendering and real encrypted provider imagery should also be checked after deployment; automated DOM checks do not establish visual image quality.
