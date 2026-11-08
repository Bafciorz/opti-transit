package pl.zabawa.optitransit.models.entities;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import lombok.*;

import java.io.Serializable;

@Embeddable
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
@EqualsAndHashCode
@ToString
public class StopTimeId implements Serializable {

    @Column(name = "trip_id", nullable = false)
    private String tripId;

    @Column(name = "stop_sequence", nullable = false)
    private Integer stopSequence;
}
