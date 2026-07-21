/*
 * Copyright 2022, Seqera Labs
 * Modifications Copyright 2026, FAIRSCAPE (nf-fairscape fork)
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package nextflow.prov

import java.nio.file.Files

import nextflow.Session
import spock.lang.Specification

class ProvObserverFactoryTest extends Specification {

    def 'should return observer' () {
        given:
        def crateFile = Files.createTempDirectory('nf-fairscape').resolve('ro-crate-metadata.json')
        def config = [
            fairscape: [
                file: crateFile.toString()
            ]
        ]
        def session = Mock(Session) {
            getConfig() >> config
        }

        when:
        def result = new ProvObserverFactory().create(session)
        then:
        result.size()==1
        result[0] instanceof ProvObserver
    }

    def 'should return no observer when disabled' () {
        given:
        def config = [
            fairscape: [
                enabled: false
            ]
        ]
        def session = Mock(Session) {
            getConfig() >> config
        }

        when:
        def result = new ProvObserverFactory().create(session)
        then:
        result.size()==0
    }

}
