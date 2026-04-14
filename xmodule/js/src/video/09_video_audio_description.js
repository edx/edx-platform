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
                this._currentSpeed = parseFloat(state.speed) || 1.0;

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
                    if (this.hasSource && this.featureEnabled) {
                        this.bindHandlers();
                    }
                    if (this.isActive) {
                        this.activate();
                    }
                },

                renderElements: function() {
                    var buttonHtml, secondaryControls;
                    if (!this.featureEnabled) {
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

                    // Continuous drift correction on every timeupdate tick (~200ms).
                    this.state.el.on('timeupdate', function(event, videoTime) {
                        if (!this.isActive || !this.audioEl) { return; }
                        if (this._blobFetchInProgress) { return; }
                        var audioElement = this.audioEl[0];
                        if (audioElement.readyState < 1) {
                            return;
                        }

                        if (typeof this._pendingSeekTarget === 'number') {
                            if (Math.abs(videoTime - this._pendingSeekTarget) < 1.0) {
                                this._pendingSeekTarget = null;
                            } else {
                                return;
                            }
                        }

                        if (!this._isWithinAdRange(videoTime)) {
                            if (!audioElement.paused) { audioElement.pause(); }
                            return;
                        }
                        var now = Date.now();
                        if (this._lastDriftCorrection && (now - this._lastDriftCorrection) < 2000) {
                            return;
                        }
                        if (audioElement.paused) {
                            if (this._seekAudioTo(audioElement, videoTime)) {
                                this._lastDriftCorrection = now;
                                this._playAudio(audioElement);
                            }
                            return;
                        }
                        var drift = Math.abs(audioElement.currentTime - videoTime);
                        if (drift > 1.5) {
                            if (videoTime < 0.1 && audioElement.currentTime < 1) { return; }
                            if (this._seekAudioTo(audioElement, videoTime)) {
                                this._lastDriftCorrection = now;
                            }
                        }
                    }.bind(this));

                    this.state.el.on('speedchange', function(event, newSpeed) {
                        var rate = parseFloat(newSpeed) || 1.0;
                        this._currentSpeed = rate;
                        this.audioEl[0].playbackRate = rate;
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

                    audioElement.playbackRate = this._currentSpeed || parseFloat(this.state.speed) || 1.0;

                    var playing = false;
                    try {
                        playing = videoPlayer && videoPlayer.isPlaying && videoPlayer.isPlaying();
                    } catch (e) {}

                    if (typeof this._pendingSeekTarget === 'number') {
                        currentTime = this._pendingSeekTarget;
                    }

                    if (playing) {
                        if (!this._isWithinAdRange(currentTime)) {
                            return;
                        }

                        var timeSinceLast = this._lastDriftCorrection
                            ? (Date.now() - this._lastDriftCorrection) : Infinity;
                        var timeSinceSync = this._lastSyncTime
                            ? (Date.now() - this._lastSyncTime) : Infinity;

                        if (timeSinceLast < 500 || timeSinceSync < 2000) {
                            this._lastDriftCorrection = Date.now();
                            if (this._blobFetchInProgress) {
                                return;
                            }
                            if (audioElement.paused
                                && this._isWithinAdRange(audioElement.currentTime)
                                && (audioElement.currentTime > 1 || currentTime < 2)) {
                                
                                this._playAudio(audioElement);
                            }
                            return;
                        }

                        var doPlay = function() {
                            self._playAudio(audioElement);
                            self._lastDriftCorrection = Date.now();
                        };

                        if (audioElement.readyState >= 1) {
                            if (this._seekAudioTo(audioElement, currentTime)) {
                                doPlay();
                            }
                        } else {
                            var onLoaded = function() {
                                audioElement.removeEventListener('loadedmetadata', onLoaded);
                                // Use pending target if available, otherwise video time.
                                var nowTime = (typeof self._pendingSeekTarget === 'number')
                                    ? self._pendingSeekTarget
                                    : ((videoPlayer && typeof videoPlayer.currentTime === 'number')
                                        ? videoPlayer.currentTime : 0);
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
                },

                syncCurrentTime: function(time) {
                    if (!this.isActive || !this.audioEl) {
                        return;
                    }

                    var t = parseFloat(time) || 0;
                    var audioElement = this.audioEl[0];
                    var self = this;

                    if (!audioElement.paused) {
                        audioElement.pause();
                    }

                    this._pendingSeekTarget = t;
                    this._lastSyncTime = Date.now();
                    this._lastDriftCorrection = Date.now();

                    if (!this._isWithinAdRange(t)) {
                        return;
                    }

                    if (audioElement.readyState < 1) {
                        var onLoaded = function() {
                            audioElement.removeEventListener('loadedmetadata', onLoaded);
                            audioElement.currentTime = t;
                            self._lastSyncTime = Date.now();
                            self._lastDriftCorrection = Date.now();
                            var playing = false;
                            try {
                                playing = self.state.videoPlayer
                                    && self.state.videoPlayer.isPlaying
                                    && self.state.videoPlayer.isPlaying();
                            } catch (e) {}
                            if (playing && self._isWithinAdRange(t)) {
                                self._playAudio(audioElement);
                            }
                        };
                        audioElement.addEventListener('loadedmetadata', onLoaded);
                        audioElement.load();
                        return;
                    }

                    if (Math.abs(audioElement.currentTime - t) < 0.3) {
                        return;
                    }

                    if (this._seekAudioTo(audioElement, t)) {
                        this._lastSyncTime = Date.now();
                        this._lastDriftCorrection = Date.now();

                        var videoPlaying = false;
                        try {
                            videoPlaying = this.state.videoPlayer
                                && this.state.videoPlayer.isPlaying
                                && this.state.videoPlayer.isPlaying();
                        } catch (e) {}

                        if (videoPlaying) { this._playAudio(audioElement); }
                    }
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

                    if (this._blobUrl) {
                        URL.revokeObjectURL(this._blobUrl);
                        this._blobUrl = null;
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

                _seekAudioTo: function(audioEl, target) {
                    if (this._blobFetchInProgress) {
                        this._pendingBlobSeekTarget = target;
                        return false;
                    }

                    audioEl.currentTime = target;
                    var after = audioEl.currentTime;
                    var canPlay = Math.abs(after - target) < 2.0;

                    if (!canPlay) {
                        audioEl.pause();
                        this._fetchAsBlobAndSeek(target);
                    }
                    return canPlay;
                },

                _fetchAsBlobAndSeek: function(target) {
                    if (this._blobFetchInProgress) {
                        this._pendingBlobSeekTarget = target;
                        return;
                    }

                    this._blobFetchInProgress = true;
                    this._pendingBlobSeekTarget = target;                    
                    var self = this;
                    var url = this.state.config.audioDescriptionUrl;
                    var xhr = new XMLHttpRequest();
                    
                    xhr.open('GET', url, true);
                    xhr.responseType = 'blob';
                    xhr.onload = function() {
                        if (xhr.status < 200 || xhr.status >= 300) {
                            self._blobFetchInProgress = false;
                            return;
                        }

                        var blob = xhr.response;
                        var blobUrl = URL.createObjectURL(blob);
                        var audioElement = self.audioEl[0];
                        var seekTarget = self._pendingBlobSeekTarget;

                        // Swap to the blob URL.
                        audioElement.src = blobUrl;
                        self._blobUrl = blobUrl;

                        audioElement.addEventListener('loadedmetadata', function onMeta() {
                            audioElement.removeEventListener('loadedmetadata', onMeta);

                            var finalTarget = self._pendingBlobSeekTarget || seekTarget;

                            audioElement.currentTime = finalTarget;
                            audioElement.playbackRate = self._currentSpeed || parseFloat(self.state.speed) || 1.0;

                            var adVolume = (typeof self._savedVideoVolume === 'number')
                                ? self._savedVideoVolume / 100 : 1;
                            audioElement.volume = adVolume;

                            self._lastSyncTime = Date.now();
                            self._lastDriftCorrection = Date.now();
                            self._blobFetchInProgress = false;
                            self._pendingBlobSeekTarget = null;

                            // Resume playback if AD is active and video is playing.
                            var playing = false;
                            try {
                                playing = self.state.videoPlayer
                                    && self.state.videoPlayer.isPlaying
                                    && self.state.videoPlayer.isPlaying();
                            } catch (e) {}

                            if (self.isActive && playing && self._isWithinAdRange(finalTarget)) {
                                self._playAudio(audioElement);
                            }
                        });

                        audioElement.load();
                    };
                    xhr.onerror = function() {
                        self._blobFetchInProgress = false;
                    };
                    xhr.send();
                },

                _logTimeRanges: function(ranges) {
                    if (!ranges || !ranges.length) { return '(empty)'; }
                    var parts = [];
                    for (var i = 0; i < ranges.length; i++) {
                        parts.push(ranges.start(i).toFixed(1) + '-' + ranges.end(i).toFixed(1));
                    }
                    return parts.join(', ');
                },

                _playAudio: function(audioEl) {
                    var rate = this._currentSpeed || parseFloat(this.state.speed) || 1.0;
                    audioEl.playbackRate = rate;
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
