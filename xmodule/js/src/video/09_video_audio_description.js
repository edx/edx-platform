/* eslint-disable no-underscore-dangle, no-param-reassign */
/**
 * @file VideoAudioDescription module — manages a secondary <audio> element
 * that plays an audio description (AD) track synchronised with the main video.
 *
 * Design:
 *   - Constructor shape, _.bindAll list, return value, and moduleName property
 *     follow the same conventions as VideoCaption (09_video_caption.js) and
 *     SaveStatePlugin (09_save_state_plugin.js).
 *   - Events object binding follows SaveStatePlugin.initialize() exactly.
 *   - destroy() follows VideoCaption.destroy() exactly.
 *   - Toggle button HTML uses HtmlUtils.interpolateHtml (07_video_volume_control.js).
 *   - User preference is persisted via state.storage.setItem + saveState
 *     (SaveStatePlugin.onSpeedChange pattern).
 *
 * WCAG 2.1 SC 1.2.5 — Audio Description (Prerecorded)
 */

(function(define) {
    'use strict';

    define(
        'video/09_video_audio_description.js',
        [
            'underscore',    // functional utilities (_)
            'gettext',       // i18n string lookup
            'edx-ui-toolkit/js/utils/html-utils'  // safe HTML building
        ],
        function(_, gettext, HtmlUtils) {
            /**
             * VideoAudioDescription constructor.
             *
             * @param {Object} state  The shared video-player state object.
             * @param {Object} i18n   Translation strings (injected by the module loader).
             * @returns {jQuery.Promise}  Resolved immediately (no async init needed).
             */
            var VideoAudioDescription = function(state) {
                // Guard against being called without `new`.
                if (!(this instanceof VideoAudioDescription)) {
                    return new VideoAudioDescription(state);
                }

                // Pre-bind all prototype methods so `this` is always correct inside callbacks.
                // Pattern copied verbatim from VideoCaption constructor (09_video_caption.js).
                _.bindAll(this,
                    'initialize',
                    'renderElements',
                    'bindHandlers',
                    'toggle',
                    'activate',
                    'deactivate',
                    'syncCurrentTime',
                    'destroy'
                );

                this.state = state;  // keep a reference for event forwarding

                // Register this module instance on the shared state so other modules can
                // discover it (e.g. tests, external extensions).
                this.state.videoAudioDescription = this;

                // Track whether a usable AD source is available.  The button is always
                // rendered for discoverability, but functionality is disabled when there
                // is no source URL configured.  Enabled is auto-derived from source presence.
                this.hasSource = !!state.config.audioDescriptionUrl;

                this.initialize();

                // Every video module must return a resolved promise so that 10_main.js can
                // use $.when() to know when all modules are ready.
                return $.Deferred().resolve().promise();
            };

            // Static moduleName used by _initializeModules() in 01_initialize.js to pass
            // per-module options from state.options.
            // Pattern: SaveStatePlugin.moduleName = 'SaveStatePlugin'
            VideoAudioDescription.moduleName = 'VideoAudioDescription';

            VideoAudioDescription.prototype = /** @lends VideoAudioDescription.prototype */ {

                // -----------------------------------------------------------------
                // Initialisation
                // -----------------------------------------------------------------

                /**
                 * Restore persisted preference, build DOM, wire events.
                 */
                initialize: function() {
                    // Restore preference from localStorage (set by saveState / storage).
                    // Falls back to the server value (audioDescriptionActive) which was read
                    // from the learner's user_state field.
                    // Only allow active state if a source is actually available.
                    this.isActive = this.hasSource
                        ? (this.state.config.audioDescriptionActive || false)
                        : false;
                    this.renderElements();  // create <audio> element and button
                    // Only wire player events when a source URL is configured.
                    if (this.hasSource) {
                        this.bindHandlers();    // wire player events
                    }
                    if (this.isActive) {
                        // Replay the activation logic without triggering a new saveState.
                        this.activate();
                    }
                },

                /**
                 * Create the hidden <audio> element and the toggle button.
                 *
                 * Button HTML built with HtmlUtils.interpolateHtml (same technique as
                 * 07_video_volume_control.js) to ensure no XSS from i18n strings.
                 */
                renderElements: function() {
                    var buttonHtml, secondaryControls;

                    // Only create the <audio> element when a source URL is configured.
                    if (this.hasSource) {
                        // Build the <audio> element.  src is assigned here from the config so that
                        // the browser starts buffering only when the module initialises (not before).
                        var audioEl = $('<audio>', {
                            id: 'audio-description-' + this.state.id, // matches the server-rendered element
                            src: this.state.config.audioDescriptionUrl,  // URL from block metadata
                            preload: 'auto',    // buffer immediately so first play has no gap
                            'aria-hidden': 'true'  // screen readers should not announce this element
                        });

                        // Prefer the pre-rendered server-side element (from video.html) to avoid a
                        // duplicate; fall back to appending a new one if not found.
                        var existing = this.state.el.find('#audio-description-' + this.state.id);
                        if (existing.length) {
                            // Update the src and preload attributes on the server-rendered placeholder.
                            // The template renders preload="none" since it has no src; switch to
                            // "auto" so the browser starts buffering the audio file immediately.
                            existing.attr('src', this.state.config.audioDescriptionUrl);
                            existing.attr('preload', 'auto');
                            this.audioEl = existing;  // store reference for later manipulation
                        } else {
                            // No server-rendered placeholder — append one now.
                            this.state.el.append(audioEl);
                            this.audioEl = audioEl;
                        }
                    }

                    // Build the toggle button using HtmlUtils.interpolateHtml so that
                    // translated strings are safely escaped (pattern: 07_video_volume_control.js).
                    // When no AD source is configured, render a disabled button for discoverability.
                    var buttonTemplate = this.hasSource
                        ? [
                            '<button class="control audio-description-toggle"',
                            ' aria-pressed="{isActive}"',
                            ' aria-label="{label}"',
                            ' title="{title}"',
                            ' type="button">',
                            '<span class="icon fa fa-audio-description" aria-hidden="true"></span>',
                            '<span class="sr">{srLabel}</span>',
                            '</button>'
                        ]
                        : [
                            '<button class="control audio-description-toggle is-disabled"',
                            ' aria-pressed="false"',
                            ' aria-label="{disabledLabel}"',
                            ' title="{disabledTitle}"',
                            ' type="button">',
                            '<span class="icon fa fa-audio-description" aria-hidden="true"></span>',
                            '<span class="sr">{disabledLabel}</span>',
                            '</button>'
                        ];

                    buttonHtml = HtmlUtils.interpolateHtml(
                        HtmlUtils.HTML(buttonTemplate.join('')),
                        {
                            isActive: String(this.isActive),                        // 'true'|'false'
                            label: gettext('Toggle audio description'),             // aria-label
                            title: gettext('Audio Description'),                    // tooltip
                            srLabel: gettext('Toggle audio description'),           // visible-only-to-SR
                            disabledLabel: gettext('Audio description not available'),  // disabled aria-label
                            disabledTitle: gettext('Audio description not available')   // disabled tooltip
                        }
                    );

                    // Place the button inside .secondary-controls alongside captions, speed, etc.
                    secondaryControls = this.state.el.find('.secondary-controls');
                    HtmlUtils.append(secondaryControls, buttonHtml);

                    // Cache the rendered DOM node for direct manipulation later.
                    this.toggleButton = secondaryControls.find('.audio-description-toggle');

                    // Reflect initial state on the button.
                    this.toggleButton.toggleClass('is-active', this.isActive);
                },

                // -----------------------------------------------------------------
                // Event binding
                // -----------------------------------------------------------------

                /**
                 * Wire player events using the events-object pattern from SaveStatePlugin.
                 *
                 * Defining this.events allows destroy() to call state.el.off(this.events)
                 * without needing to track individual listeners — identical to SaveStatePlugin.
                 */
                bindHandlers: function() {
                    // Declare all handlers in a map so destroy() can clean them up atomically.
                    this.events = {
                        play: this.activate,            // start AD when video plays
                        pause: this.deactivate,         // pause AD when video pauses
                        ended: this.deactivate,         // stop AD when video ends
                        destroy: this.destroy           // clean up when the player is torn down
                    };

                    // Bind the event map — reuse state.el.on(this.events) from SaveStatePlugin.bindHandlers.
                    this.state.el.on(this.events);

                    // Seek events carry a `time` argument, so they need a separate listener.
                    this.state.el.on('seek', function(event, time) {
                        // Sync AD playhead to the new video position.
                        this.syncCurrentTime(time);
                    }.bind(this));

                    // Continuous drift correction: the video player fires 'timeupdate' every
                    // ~250ms with the video's current playhead position.  If the AD audio has
                    // drifted more than the tolerance threshold, snap it back into sync.
                    this.state.el.on('timeupdate', function(event, videoTime) {
                        if (!this.isActive || !this.audioEl) {
                            return;  // AD not active — nothing to sync
                        }
                        var audioElement = this.audioEl[0];
                        // Skip if metadata hasn't loaded — can't meaningfully correct drift.
                        if (audioElement.readyState < 1) {
                            return;
                        }
                        // If the video has progressed past the AD audio duration, pause the
                        // AD audio (the description content for this portion is finished).
                        if (!this._isWithinAdRange(videoTime)) {
                            if (!audioElement.paused) {
                                audioElement.pause();
                            }
                            return;  // nothing to sync — AD content exhausted
                        }
                        // If the audio is paused but we're within AD range, the user may
                        // have seeked back into range — resume playback.
                        if (audioElement.paused) {
                            audioElement.currentTime = videoTime;
                            this._playAudio(audioElement);
                            this._lastDriftCorrection = Date.now();
                            return;
                        }
                        // Throttle drift correction: skip if we corrected within the last 800ms
                        // to prevent rapid-fire seeks that cause audio glitches (especially near 0).
                        var now = Date.now();
                        if (this._lastDriftCorrection && (now - this._lastDriftCorrection) < 800) {
                            return;
                        }
                        var drift = Math.abs(audioElement.currentTime - videoTime);
                        // Tolerance: 0.5 seconds — small enough that drift is imperceptible,
                        // but large enough to avoid constant seeking on every timeupdate tick.
                        if (drift > 0.5) {
                            // Skip drift correction if the audio would seek to the very start,
                            // as setting currentTime near 0 on some browsers triggers a
                            // media reload loop that manifests as infinite audio glitching.
                            if (videoTime < 0.1 && audioElement.currentTime < 1) {
                                return;  // near start — let natural playback handle it
                            }
                            audioElement.currentTime = videoTime;
                            this._lastDriftCorrection = now;
                        }
                    }.bind(this));

                    // Speed-change events carry the new rate so AD sounds natural.
                    this.state.el.on('speedchange', function(event, newSpeed) {
                        // Match AD playback rate to video playback rate.
                        this.audioEl[0].playbackRate = parseFloat(newSpeed) || 1.0;
                    }.bind(this));

                    // When the user changes volume while AD is active, route the new
                    // volume to the AD audio element as well.  Skip when the volume
                    // change was triggered by _muteVideo() (setting the video to 0).
                    this.state.el.on('volumechange', function(event, volume) {
                        if (this.isActive && !this._videoMuted) {
                            // Apply the user's chosen volume level to the AD audio (0-100 → 0-1).
                            this.audioEl[0].volume = (volume || 0) / 100;
                        }
                    }.bind(this));

                    // User clicks the toggle button.
                    this.toggleButton.on('click', this.toggle);
                },

                // -----------------------------------------------------------------
                // Toggle / Activate / Deactivate
                // -----------------------------------------------------------------

                /**
                 * Toggle the AD track on or off and persist the preference.
                 */
                toggle: function() {
                    // Do nothing when no AD source is configured (button is disabled).
                    if (!this.hasSource) {
                        return;
                    }
                    this.isActive = !this.isActive;   // flip state

                    // Update ARIA attribute so screen readers announce the new state.
                    this.toggleButton.attr('aria-pressed', String(this.isActive));

                    // Toggle the visual active-state class.
                    this.toggleButton.toggleClass('is-active', this.isActive);

                    if (this.isActive) {
                        this.activate();   // start audio and sync
                    } else {
                        this.deactivate(); // pause audio
                        // Unmute only on explicit toggle-off, not on transient pause/ended events.
                        this._unmuteVideo();
                    }

                    // Persist preference using storage.setItem + saveState.
                    // This mirrors SaveStatePlugin.onSpeedChange — do NOT use raw $.ajax.
                    this.state.storage.setItem('audio_description_active', this.isActive);
                    this._saveAdState();
                },

                /**
                 * Start the AD track, synced to the current video position.
                 * Called on 'play' event and when toggle() enables the feature.
                 * Mutes the original video audio so only the AD track is heard.
                 */
                activate: function() {
                    var videoPlayer, currentTime, audioElement, self;

                    if (!this.isActive) {
                        return;  // nothing to do if AD is not enabled
                    }

                    audioElement = this.audioEl[0];  // native HTMLAudioElement reference
                    self = this;

                    // Mute the original video audio so only the AD track is heard.
                    // Must mute FIRST so _savedVideoVolume captures the real level.
                    this._muteVideo();

                    // Use the saved pre-mute volume for AD audio level, because
                    // volumeControl.getVolume() returns 0 after muting.  This
                    // prevents the AD audio from going silent on subsequent
                    // play events (pause→play cycle).
                    var adVolume = (typeof this._savedVideoVolume === 'number')
                        ? this._savedVideoVolume / 100
                        : 1;

                    // Route the pre-mute volume level to the AD audio element.
                    audioElement.volume = adVolume;

                    // Determine the video's current playhead position for syncing.
                    videoPlayer = this.state.videoPlayer;
                    currentTime = (videoPlayer && typeof videoPlayer.currentTime === 'number')
                        ? videoPlayer.currentTime
                        : 0;

                    // Match current playback rate so speed changes that happened before
                    // activation are honoured.
                    audioElement.playbackRate = parseFloat(this.state.speed) || 1.0;

                    // Only play if the video itself is currently playing; the 'play' event
                    // listener handles the case where the user presses play after toggling.
                    // Guard: videoPlayer.isPlaying can throw if the underlying player
                    // (YouTube/HTML5) hasn't finished initialising yet.
                    var playing = false;
                    try {
                        playing = videoPlayer && videoPlayer.isPlaying && videoPlayer.isPlaying();
                    } catch (e) {
                        // Player not ready — treat as not playing; the 'play' event will sync later.
                    }
                    if (playing) {
                        // Guard: if the video position exceeds the AD audio duration, the AD
                        // content for this portion of the video has finished.  Don't try to
                        // play — seeking past the end causes the browser to clamp to the
                        // final sample and play() does nothing.  The video stays muted (AD
                        // mode is active); playback will resume if the user seeks back.
                        if (!this._isWithinAdRange(currentTime)) {
                            return;  // past AD content — nothing to play at this position
                        }
                        // If the audio has enough data loaded, seek then play immediately.
                        // readyState >= 1 (HAVE_METADATA) means duration and seekable ranges
                        // are available, so setting currentTime won't error.
                        if (audioElement.readyState >= 1) {
                            audioElement.currentTime = currentTime;  // sync AD to video position
                            self._playAudio(audioElement);
                        } else {
                            // Audio data not loaded yet (preload="none" or slow network).
                            // Wait for 'loadedmetadata' before seeking and playing.
                            var onLoaded = function() {
                                audioElement.removeEventListener('loadedmetadata', onLoaded);
                                // Re-check range after async wait — video may have moved.
                                var nowTime = (videoPlayer && typeof videoPlayer.currentTime === 'number')
                                    ? videoPlayer.currentTime : 0;
                                if (!self._isWithinAdRange(nowTime)) {
                                    return;  // video progressed past AD audio range
                                }
                                audioElement.currentTime = nowTime;
                                self._playAudio(audioElement);
                            };
                            audioElement.addEventListener('loadedmetadata', onLoaded);
                            // Trigger loading if the browser hasn't started yet.
                            audioElement.load();
                        }
                    }
                },

                /**
                 * Pause the AD track.  Called on 'pause', 'ended', and when toggle() disables.
                 * Only pauses the AD audio — does NOT unmute the video here, because pause/ended
                 * events are transient (user may resume).  toggle() handles unmuting explicitly.
                 */
                deactivate: function() {
                    this.audioEl[0].pause();  // pause — does not reset currentTime
                },

                /**
                 * Sync the AD playhead to a given time value (called on video 'seek').
                 *
                 * @param {number} time  The target time in seconds.
                 */
                syncCurrentTime: function(time) {
                    if (this.isActive && this.audioEl) {
                        var t = parseFloat(time) || 0;
                        var audioElement = this.audioEl[0];
                        // Guard: only seek if the audio has loaded metadata, otherwise
                        // setting currentTime on a short file at position 0 can cause
                        // the browser to restart() the load and create an infinite loop.
                        if (audioElement.readyState < 1) {
                            return;  // metadata not loaded — skip seek entirely
                        }
                        // If seeking past the AD audio duration, pause audio (AD finished
                        // for this portion).  If seeking back INTO the AD range, resume.
                        if (!this._isWithinAdRange(t)) {
                            // Past the AD content — pause if still playing.
                            if (!audioElement.paused) {
                                audioElement.pause();
                            }
                            return;
                        }
                        // Skip redundant seeks: if the audio is already close to the target,
                        // don't re-seek.  This prevents a browser re-load/restart loop at
                        // position 0 that manifests as an infinite audio glitch.
                        if (Math.abs(audioElement.currentTime - t) < 0.3) {
                            return;  // already close enough — no seek needed
                        }
                        audioElement.currentTime = t;
                        // Always resume AD audio after seeking when the video is
                        // playing.  Setting currentTime on a network-loaded audio
                        // element can cause the browser to pause for buffering, and
                        // in YouTube mode no 'play' event fires after a seek, so
                        // without an explicit play() the AD stays silent.
                        var videoPlaying = false;
                        try {
                            videoPlaying = this.state.videoPlayer
                                && this.state.videoPlayer.isPlaying
                                && this.state.videoPlayer.isPlaying();
                        } catch (e) {
                            // Player not ready — ignore.
                        }
                        if (videoPlaying) {
                            this._playAudio(audioElement);
                        }
                    }
                },

                // -----------------------------------------------------------------
                // Cleanup / Destroy
                // -----------------------------------------------------------------

                /**
                 * Tear down: remove event listeners, DOM elements, and the state reference.
                 *
                 * Pattern copied from VideoCaption.destroy() — use state.el.off(this.events)
                 * to remove all listeners atomically, then clean up DOM and state ref.
                 */
                destroy: function() {
                    // Restore original video volume if AD was active when the player is torn down.
                    if (this.isActive) {
                        this._unmuteVideo();
                    }

                    // Only remove event listeners if they were bound (source available).
                    if (this.events) {
                        // Remove all listeners registered via the events map in one call.
                        // Identical to SaveStatePlugin.destroy() / VideoCaption.destroy().
                        this.state.el.off(this.events);

                        // Remove the individually bound seek, speedchange, volumechange,
                        // and timeupdate listeners.
                        this.state.el.off('seek');
                        this.state.el.off('speedchange');
                        this.state.el.off('volumechange');
                        this.state.el.off('timeupdate');
                    }

                    // Remove the toggle button click listener.
                    if (this.toggleButton) {
                        this.toggleButton.off('click', this.toggle);

                        // Remove DOM elements we created so there is no lingering markup.
                        this.toggleButton.remove();
                    }

                    // Only remove the audio element if it exists and we created it ourselves.
                    // If we adopted the server-rendered element we leave it in place.
                    if (this.audioEl && !this.state.el.find('#audio-description-' + this.state.id).length) {
                        this.audioEl.remove();
                    }

                    // Drop the state reference so the GC can collect this module.
                    delete this.state.videoAudioDescription;
                },

                // -----------------------------------------------------------------
                // Private helpers
                // -----------------------------------------------------------------

                /**
                 * Mute the original video audio by setting its volume to 0.
                 * Stores the previous volume so _unmuteVideo() can restore it.
                 * Works for both YouTube (setVolume 0-100) and HTML5 players.
                 *
                 * @private
                 */
                _muteVideo: function() {
                    var volumeControl = this.state.videoVolumeControl;
                    if (!volumeControl) {
                        return;  // volume control not ready yet
                    }
                    // Only save the volume on the FIRST mute call.  activate() can be
                    // called multiple times (toggle + play event), so a second call would
                    // overwrite the saved value with 0 (already muted), making _unmuteVideo
                    // restore to silence instead of the real volume.
                    if (!this._videoMuted) {
                        this._savedVideoVolume = volumeControl.getVolume();
                        this._videoMuted = true;
                    }
                    // Mute via the volume control module so the slider, cookie, and UI stay in sync.
                    volumeControl.setVolume(0, true, false);
                    // Also set the underlying player directly for immediate effect.
                    var player = this.state.videoPlayer && this.state.videoPlayer.player;
                    if (player && typeof player.setVolume === 'function') {
                        player.setVolume(0);
                    }
                },

                /**
                 * Restore the original video volume that was saved by _muteVideo().
                 * Falls back to 100 if no saved value exists.
                 *
                 * @private
                 */
                _unmuteVideo: function() {
                    var volumeControl = this.state.videoVolumeControl;
                    if (!volumeControl) {
                        return;  // volume control not ready yet
                    }
                    // Restore the video volume to the level before AD was activated.
                    var restoredVolume = (typeof this._savedVideoVolume === 'number')
                        ? this._savedVideoVolume
                        : 100;
                    // Reset the muted flag BEFORE setVolume so event handlers see
                    // the correct state when the volumechange event fires.
                    this._videoMuted = false;
                    // Restore via the volume control module so slider, cookie, and UI update.
                    volumeControl.setVolume(restoredVolume, false, false);
                    // Also set the underlying player directly for guaranteed restoration,
                    // mirroring _muteVideo() which sets player.setVolume(0) directly.
                    var player = this.state.videoPlayer && this.state.videoPlayer.player;
                    if (player && typeof player.setVolume === 'function') {
                        player.setVolume(restoredVolume);
                    }
                },

                /**
                 * Check whether a given time falls within the AD audio's playable range.
                 * Returns false if the time exceeds the audio's duration (AD content is
                 * exhausted for that video position) or if duration is not yet known.
                 *
                 * @param {number} time  Time in seconds to check.
                 * @returns {boolean}  True if the time is within [0, duration).
                 * @private
                 */
                _isWithinAdRange: function(time) {
                    if (!this.audioEl) {
                        return false;
                    }
                    var dur = this.audioEl[0].duration;
                    // If duration is unknown (NaN/Infinity), assume in range.
                    if (!isFinite(dur) || dur <= 0) {
                        return true;
                    }
                    // Allow a small buffer (0.1s) so we don't cut off the very last sample.
                    return time < dur - 0.1;
                },

                /**
                 * Play the audio element, handling the Promise rejection gracefully.
                 * Logs actual errors (network, decode) so failures aren't swallowed.
                 *
                 * @param {HTMLAudioElement} audioEl  The native audio element to play.
                 * @private
                 */
                _playAudio: function(audioEl) {
                    var playPromise = audioEl.play();
                    if (playPromise !== undefined) {
                        playPromise.catch(function(err) {
                            // NotAllowedError = autoplay policy — expected, ignore.
                            // Other errors (NotSupportedError, AbortError) indicate real problems.
                            if (err.name !== 'NotAllowedError') {
                                console.warn('[AudioDescription] playback failed:', err.name, err.message);
                            }
                        });
                    }
                },

                /**
                 * Proxy to SaveStatePlugin.saveState when available, so the save goes through
                 * the standard debounce/batching logic.  Falls through to a direct handler
                 * URL call when the plugin is not present (e.g. unit tests).
                 *
                 * @private
                 */
                _saveAdState: function() {
                    if (this.state.videoSaveStatePlugin) {
                        // Preferred path: delegate to the plugin that already manages saveStateUrl.
                        this.state.videoSaveStatePlugin.saveState(true, {
                            audio_description_active: this.isActive
                        });
                    } else if (this.state.config.saveStateUrl) {
                        // Fallback: direct XHR to the handler URL (e.g. in tests without the plugin).
                        $.ajax({
                            url: this.state.config.saveStateUrl,  // /handler/save_user_state
                            type: 'POST',
                            data: {audio_description_active: this.isActive}
                        });
                    }
                }
            };

            return VideoAudioDescription;
        }
    );
}(RequireJS.define));
