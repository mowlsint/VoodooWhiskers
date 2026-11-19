# Satellite images on the main map

The SAR workbench opens in a separate tab (`target="_blank"`). The main map's **Satellite images** button opens a movable, non-modal window. Drag its header or use the arrow keys while the header has focus. Closing the window keeps the selected overlays visible; **Reset overview** hides them without discarding the loaded images.

## Open an image from a manual GitHub workflow

1. Open the completed **SAR Workbench · encrypted request** run in GitHub Actions.
2. In the run summary, under **Artifacts**, click **sar-workbench-encrypted**. It is produced by the **Save encrypted state for at most fourteen days** step and downloads as a ZIP.
3. On the main map, open **Satellite images → Load a result from GitHub Actions**. Select the ZIP directly; **do not extract it**.
4. Enter the workspace password and click **Open result ZIP and show image**.

The browser decrypts the ZIP locally. The file and decrypted image are not uploaded. The first image is displayed and the map fits its area automatically. Use the image selector if the workspace contains more than one completed preview.

A completed full-scene overview can also be transferred automatically when its encrypted results ZIP is opened in the linked workbench. If no map tab is linked, the handoff opens a new main-map tab. Full-scene overviews are contextual previews, not vessel-scale detail. Only the selected preview and its scene/time/processed-area metadata are transferred. Passwords, GitHub tokens, candidates and analyst notes are not transferred.

Use **Show processed image footprint** and **Show SAR image preview** independently to change visibility. **Fit image area** remains available to refit the map to the selected image.

**Download PNG** saves the existing derived Sentinel-1 VV quicklook. **Download footprint** saves a GeoJSON polygon with the acquisition time, scene identifier and preview dimensions. This polygon describes the processed request area, not the entire satellite acquisition footprint. The quicklook is limited to 1,200 pixels per side and 2 MiB. The processing pipeline deletes the temporary raw GeoTIFF; this feature cannot download it or reconstruct a full-resolution scene.

## Local handling and analytical limits

- At most six previews are held in a map tab. No image or decrypted workspace is written to repository files, browser storage or a server. Downloading is an explicit local action.
- **Remove image** removes one image. **Clear images / lock**, reloading or closing the tab removes the loaded previews. Locking the separate workbench cancels pending transfers but does not remove an image already sent to the main map; clear that tab separately.
- Existing encrypted artifact expiry, acquisition limits, workflows and public data contracts remain unchanged. Opening this window does not collect imagery or start Actions.
- Image acquisition time is separate from vessel observation time. Overlay proximity is not evidence that a vessel was present during acquisition. No candidate/AIS association, detection gate or score is changed.
- Invalid, oversized, unsupported or incorrectly decrypted inputs produce an explicit error while preserving previously loaded images. Historical valid images remain visibly timestamped; no current-image claim is made.

## Local validation

Node 24 unit tests cover bounded image/georeference validation, transfer allowlisting, actual encrypted workspace decryption, session-scoped tab messaging, cancellation and the new-tab fallback. The map DOM tests use jsdom and real Leaflet to exercise independent overlays, opacity, downloads, clearing, the six-image cap, map reset and existing vessel search without provider calls. Browser rendering and real encrypted provider imagery should also be checked after deployment; automated DOM checks do not establish visual image quality.
