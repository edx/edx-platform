/* eslint-disable no-underscore-dangle, no-param-reassign */
/**
 * @module VideoAudioDescription
 * Manages a secondary <audio> element synchronised with the main video.
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
            var VideoAudioDescription = function(state) {
                if (!(this instanceof VideoAudioDescription)) {
                    return new VideoAudioDescription(state);
                }

                _.bindAll(this,
                    'initialize', 'renderElements', 'bindHandlers',
                    'toggle', 'activate', 'deactivate', 'syncCurrentTime', 'destroy'
                );

                this.state = state;
                this.state.videoAudioDescription = this;
                this.hasSource = !!state.config.audioDescriptionUrl;
                this.featureEnabled = state.config.audioDescriptionEnabled;

                this.initialize();

                return $.Deferred().resolve().promise();
            };

            VideoAudioDescription.moduleName = 'VideoAudioDescription';

            VideoAudioDescription.prototype = {

                initialize: function() {
                    this.isActive = this.hasSource
                        ? (this.state.config.audioDescriptionActive || false)
                        : false;
                    this.renderElements();
                    if (this.hasSource) {
                        this.bindHandlers();
                    }
                    if (this.isActive) {
                        this.activate();
                    }
                },

                renderElements: function() {
                    var buttonHtml, secondaryControls;
                    if (!this.featureEnabled && !this.hasSource) {
                        return;
                    }

                    if (this.hasSource) {
                        var audioEl = $('<audio>', {
                            id: 'audio-description-' + this.state.id,
                            src: this.state.config.audioDescriptionUrl,
                            preload: 'auto',
                            'aria-hidden': 'true'
                        });

                        var existing = this.state.el.find('#audio-description-' + this.state.id);
                        if (existing.length) {
                            existing.attr('src', this.state.config.audioDescriptionUrl);
                            existing.attr('preload', 'auto');
                            this.audioEl = existing;
                        } else {
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
                            isActive: String(this.isActive),
                            label: gettext('Toggle audio description'),
                            title: gettext('Audio Description'),
                            srLabel: gettext('Toggle audio description'),
                            disabledLabel: gettext('Audio description not available'),
                            disabledTitle: gettext('Audio description not available')
                        }
                    );

                    secondaryControls = this.state.el.find('.secondary-controls');
                    HtmlUtils.append(secondaryControls, buttonHtml);
                    this.toggleButton = secondaryControls.find('.audio-description-toggle');
                    this.toggleButton.toggleClass('is-active', this.isActive);
                },

                bindHandlers: function() {
                    this.events = {
                        play: this.activate,
                        pause: this.deactivate,
                        ended: this.deactivate,
                        destroy: this.destroy
                    };
                    this.state.el.on(this.events);

                    this.state.el.on('seek', function(event, time) {
                        this.syncCurrentTime(time);
                    }.bind(this));

                    // Continuous drift correction on every timeupdate tick (~250ms).
                    this.state.el.on('timeupdate', function(event, videoTime) {
                        if (!this.isActive || !this.audioEl) { return; }
                        var audioElement = this.audioEl[0];
                        if (audioElement.readyState < 1) { return; }
                        if (!this._isWithinAdRange(videoTime)) {
                            if (!audioElement.paused) { audioElement.pause(); }
                            return;
                        }
                        var now = Date.now();
                        if (this._lastDriftCorrection && (now - this._lastDriftCorrection) < 800) {
                            return;
                        }
                        if (audioElement.paused) {
                            audioElement.currentTime = videoTime;
                            this._lastDriftCorrection = now;
                            this._playAudio(audioElement);
                            return;
                        }
                        var drift = Math.abs(audioElement.currentTime - videoTime);
                        if (drift > 0.5) {
                            if (videoTime < 0.1 && audioElement.currentTime < 1) { return; }
                            audioElement.currentTime = videoTime;
                            this._lastDriftCorrection = now;
                        }
                    }.bind(this));

                    this.state.el.on('speedchange', function(event, newSpeed) {
                        this.audioEl[0].playbackRate = parseFloat(newSpeed) || 1.0;
                    }.bind(this));

                    this.state.el.on('volumechange', function(event, volume) {
                        if (this.isActive && !this._videoMuted) {
                            this.audioEl[0].volume = (volume || 0) / 100;
                        }
                    }.bind(this));

                    this.toggleButton.on('click', this.toggle);
                },

                toggle: function() {
                    if (!this.hasSource) { return; }

                    this.isActive = !this.isActive;
                    this.toggleButton.attr('aria-pressed', String(this.isActive));
                    this.toggleButton.toggleClass('is-active', this.isActive);

                    if (this.isActive) {
                        this.activate();
                    } else {
                        this.deactivate();
                        this._unmuteVideo();
                    }

                    this.state.storage.setItem('audio_description_active', this.isActive);
                    this._saveAdState();
                },

                activate: function() {
                    var videoPlayer, currentTime, audioElement, self;

                    if (!this.isActive || !this.audioEl) { return; }

                    audioElement = this.audioEl[0];
                    self = this;

                    this._muteVideo();

                    // Use the pre-mute volume so AD audio isn't silent after pause→play.
                    var adVolume = (typeof this._savedVideoVolume === 'number')
                        ? this._savedVideoVolume / 100
                        : 1;
                    audioElement.volume = adVolume;

                    videoPlayer = this.state.videoPlayer;
                    currentTime = (videoPlayer && typeof videoPlayer.currentTime === 'number')
                        ? videoPlayer.currentTime : 0;

                    audioElement.playbackRate = parseFloat(this.state.speed) || 1.0;

                    var playing = false;
                    try {
                        playing = videoPlayer && videoPlayer.isPlaying && videoPlayer.isPlaying();
                    } catch (e) {}

                    if (playing) {
                        if (!this._isWithinAdRange(currentTime)) { return; }

                        var timeSinceLast = this._lastDriftCorrection
                            ? (Date.now() - this._lastDriftCorrection) : Infinity;
                        if (timeSinceLast < 500) {
                            this._lastDriftCorrection = Date.now();
                            return;
                        }

                        var timeSinceSync = this._lastSyncTime ? (Date.now() - this._lastSyncTime) : Infinity;
                        if (!audioElement.paused && timeSinceSync < 1000) {
                            this._lastDriftCorrection = Date.now();
                            return;
                        }

                        var doPlay = function() {
                            self._playAudio(audioElement);
                            self._lastDriftCorrection = Date.now();
                        };

                        if (timeSinceSync < 500) {
                            if (audioElement.paused) {
                                doPlay();
                            } else {
                                self._lastDriftCorrection = Date.now();
                            }
                        } else if (audioElement.readyState >= 1) {
                            audioElement.currentTime = currentTime;
                            doPlay();
                        } else {
                            var onLoaded = function() {
                                audioElement.removeEventListener('loadedmetadata', onLoaded);
                                var nowTime = (videoPlayer && typeof videoPlayer.currentTime === 'number')
                                    ? videoPlayer.currentTime : 0;
                                if (!self._isWithinAdRange(nowTime)) { return; }
                                audioElement.currentTime = nowTime;
                                doPlay();
                            };
                            audioElement.addEventListener('loadedmetadata', onLoaded);
                            audioElement.load();
                        }
                    }
                },

                deactivate: function() {
                    if (this.audioEl) {
                        this.audioEl[0].pause();
                    }
                    this._lastDriftCorrection = null;
                },

                syncCurrentTime: function(time) {
                    if (!this.isActive || !this.audioEl) { return; }

                    var t = parseFloat(time) || 0;
                    var audioElement = this.audioEl[0];

                    if (audioElement.readyState < 1) { return; }

                    if (!this._isWithinAdRange(t)) {
                        if (!audioElement.paused) { audioElement.pause(); }
                        return;
                    }
                    if (Math.abs(audioElement.currentTime - t) < 0.3) { return; }

                    audioElement.currentTime = t;
                    this._lastSyncTime = Date.now();
                    this._lastDriftCorrection = Date.now();

                    var videoPlaying = false;
                    try {
                        videoPlaying = this.state.videoPlayer
                            && this.state.videoPlayer.isPlaying
                            && this.state.videoPlayer.isPlaying();
                    } catch (e) {}

                    if (videoPlaying) { this._playAudio(audioElement); }
                },

                // -- Cleanup --

                destroy: function() {
                    if (this.isActive) { this._unmuteVideo(); }

                    if (this.events) {
                        this.state.el.off(this.events);
                        this.state.el.off('seek');
                        this.state.el.off('speedchange');
                        this.state.el.off('volumechange');
                        this.state.el.off('timeupdate');
                    }

                    if (this.toggleButton) {
                        this.toggleButton.off('click', this.toggle);
                        this.toggleButton.remove();
                    }

                    if (this.audioEl && !this.state.el.find('#audio-description-' + this.state.id).length) {
                        this.audioEl.remove();
                    }

                    delete this.state.videoAudioDescription;
                },

                // -- Private helpers --

                _muteVideo: function() {
                    var volumeControl = this.state.videoVolumeControl;
                    if (!volumeControl) { return; }
                    // Save volume only on first mute so we don't overwrite with 0.
                    if (!this._videoMuted) {
                        this._savedVideoVolume = volumeControl.getVolume();
                        this._videoMuted = true;
                    }
                    volumeControl.setVolume(0, true, false);
                    var player = this.state.videoPlayer && this.state.videoPlayer.player;
                    if (player && typeof player.setVolume === 'function') {
                        player.setVolume(0);
                    }
                },

                _unmuteVideo: function() {
                    var volumeControl = this.state.videoVolumeControl;
                    if (!volumeControl) { return; }
                    var restoredVolume = (typeof this._savedVideoVolume === 'number')
                        ? this._savedVideoVolume : 100;
                    this._videoMuted = false;
                    volumeControl.setVolume(restoredVolume, false, false);
                    var player = this.state.videoPlayer && this.state.videoPlayer.player;
                    if (player && typeof player.setVolume === 'function') {
                        player.setVolume(restoredVolume);
                    }
                },

                _isWithinAdRange: function(time) {
                    if (!this.audioEl) { return false; }
                    var dur = this.audioEl[0].duration;
                    if (!isFinite(dur) || dur <= 0) { return true; }
                    return time < dur - 0.1;
                },

                _playAudio: function(audioEl) {
                    var playPromise = audioEl.play();
                    if (playPromise !== undefined) {
                        playPromise.catch(function(err) {
                            // NotAllowedError = autoplay policy; ignore it.
                            if (err.name !== 'NotAllowedError') {
                                console.warn('[AudioDescription] playback failed:', err.name, err.message);
                            }
                        });
                    }
                },

                _saveAdState: function() {
                    if (this.state.videoSaveStatePlugin) {
                        this.state.videoSaveStatePlugin.saveState(true, {
                            audio_description_active: this.isActive
                        });
                    } else if (this.state.config.saveStateUrl) {
                        $.ajax({
                            url: this.state.config.saveStateUrl,
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
